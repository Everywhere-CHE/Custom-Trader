"""基于 openctp-ctp 绑定的 CTP 期货网关。

连接 CTP 兼容柜台（SimNow 仿真环境，或任意真实期货公司前置）。
设计遵循 vnpy_ctp 的 CtpGateway：

- ``CtpMdSpi``  : 行情前置（登录 + 订阅 + Tick行情推送）
- ``CtpTdSpi``  : 交易前置（认证 -> 登录 -> 结算单确认 ->
  合约查询 -> 订单/成交流程，账户与持仓轮询）
- 订单号约定：``frontid_sessionid_orderref``，使订单在重连后仍可
  撤销（与 vn.py 相同）。

所有 CTP 回调都到达于 C++ 线程；它们只构建数据对象并将事件放入
EventEngine（其设计本身即线程安全）。
"""

import logging
import os
import threading
from datetime import datetime
from typing import Any

from openctp_ctp import mdapi, tdapi

from ...constant import Direction, Exchange, Interval, Offset, OrderType, Product, Status
from ...event import EVENT_TIMER, Event, EventEngine
from ...object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)
from ...utility import CHINA_TZ, bucket_start, now_cn, round_to
from ..base import BaseGateway
from .mapping import (
    DIRECTION_MAP,
    DIRECTION_MAP_REVERSE,
    EXCHANGE_MAP,
    OFFSET_MAP,
    OFFSET_MAP_REVERSE,
    ORDERTYPE_MAP,
    ORDERTYPE_MAP_REVERSE,
    POS_DIRECTION_MAP,
    PRODUCT_MAP,
    STATUS_MAP,
    adjust_price,
    get_cn_name,
)


class CtpGateway(BaseGateway):
    """CTP 期货网关（兼容 SimNow）。"""

    default_name = "CTP"

    default_setting: dict[str, str | int | float | bool] = {
        "userid": "",
        "password": "",
        "brokerid": "9999",
        "td_address": "",
        "md_address": "",
        "appid": "simnow_client_test",
        "auth_code": "0000000000000000",
    }

    exchanges: list[Exchange] = list(EXCHANGE_MAP.values())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)

        self.md_spi: CtpMdSpi | None = None
        self.td_spi: CtpTdSpi | None = None

        self._query_count = 0
        self._timer_registered = False

    def connect(self, setting: dict) -> None:
        """启动行情（MD）和交易（TD）连接。"""
        merged: dict[str, Any] = {**self.default_setting, **setting}

        userid = str(merged["userid"])
        password = str(merged["password"])
        brokerid = str(merged["brokerid"])
        td_address = str(merged["td_address"])
        md_address = str(merged["md_address"])
        appid = str(merged["appid"])
        auth_code = str(merged["auth_code"])

        if not userid or not password:
            self.write_log(
                "CTP connect failed: userid/password missing",
                level=logging.ERROR,
            )
            return
        if not td_address:
            self.write_log(
                "CTP connect failed: td_address missing "
                "(set CTP_TD_ADDRESS in .env)",
                level=logging.ERROR,
            )
            return
        if not md_address:
            # md_address 留空 = 仅交易（TD），不连接行情（MD）。
            # 用于：模拟柜台只做下单测试、真实行情由其它数据源
            # （FUYAO 分时）提供的场景。
            self.write_log(
                "CTP md_address empty: trading only, market data disabled "
                "(quotes are served by other gateways)",
            )

        if not td_address.startswith("tcp://"):
            td_address = "tcp://" + td_address
        if md_address and not md_address.startswith("tcp://"):
            md_address = "tcp://" + md_address

        # 关闭旧连接，使新的地址 / 凭证生效
        if self.td_spi is not None:
            self.td_spi.close()
            self.td_spi = None
        if self.md_spi is not None:
            self.md_spi.close()
            self.md_spi = None

        self.td_spi = CtpTdSpi(
            self, userid, password, brokerid, auth_code, appid
        )
        # md_address 留空 = 仅交易（不连行情，行情由其它数据源提供）
        if md_address:
            self.md_spi = CtpMdSpi(self, userid, password, brokerid)

        self.td_spi.connect(td_address)
        if self.md_spi is not None:
            self.md_spi.connect(md_address)

        if not self._timer_registered:
            self.event_engine.register(EVENT_TIMER, self.process_timer_event)
            self._timer_registered = True

    def close(self) -> None:
        """释放两个 API。"""
        if self._timer_registered:
            self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
            self._timer_registered = False

        if self.md_spi:
            self.md_spi.close()
            self.md_spi = None
        if self.td_spi:
            self.td_spi.close()
            self.td_spi = None

        if self.connected:
            self.connected = False
            self.on_gateway_status()

    def subscribe(self, req: SubscribeRequest) -> None:
        # 如果合约尚未知晓，则向 CTP 柜台查询该合约
        if self.td_spi and req.symbol not in self.td_spi.contracts:
            self.td_spi.query_instrument(req.symbol)
        if self.md_spi:
            self.md_spi.subscribe(req)

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """CTP API 不提供历史 K 线数据。"""
        return []

    def send_order(self, req: OrderRequest) -> str:
        if self.td_spi:
            return self.td_spi.send_order(req)
        return ""

    def cancel_order(self, req: CancelRequest) -> None:
        if self.td_spi:
            self.td_spi.cancel_order(req)

    def query_account(self) -> None:
        if self.td_spi:
            self.td_spi.query_account()

    def query_position(self) -> None:
        if self.td_spi:
            self.td_spi.query_position()

    def process_timer_event(self, event: Event) -> None:
        """交替轮询账户 / 持仓（受 CTP 查询流控限制）。"""
        if not (self.td_spi and self.td_spi.login_ok):
            return
        self._query_count += 1
        if self._query_count % 4 == 0:
            self.query_account()
        elif self._query_count % 4 == 2:
            self.query_position()

    def update_connected(self) -> None:
        """根据前置状态重算整体连接状态（MD 停用时仅看 TD）。"""
        td_ok = bool(self.td_spi and self.td_spi.login_ok)
        md_ok = (self.md_spi is None) or bool(self.md_spi.login_ok)
        new_state = td_ok and md_ok
        if new_state != self.connected:
            self.connected = new_state
            self.on_gateway_status()


class CtpMdSpi(mdapi.CThostFtdcMdSpi):
    """行情前置 SPI。"""

    def __init__(
        self,
        gateway: CtpGateway,
        userid: str,
        password: str,
        brokerid: str,
    ) -> None:
        super().__init__()
        self.gateway = gateway
        self.userid = userid
        self.password = password
        self.brokerid = brokerid

        self.api: mdapi.CThostFtdcMdApi | None = None
        self.connect_ok = False
        self.login_ok = False
        self.reqid = 0
        # 已请求的合约代码集合，以便重连后重新订阅
        self.subscribed: set[str] = set()

    # ----- 生命周期 -----

    def connect(self, address: str) -> None:
        if self.api:
            return
        # CTP 流文件（.con）生成到 data/ 目录，避免污染 exe 所在目录
        flow_path = os.path.join(os.getcwd(), "data") + os.sep
        self.api = mdapi.CThostFtdcMdApi.CreateFtdcMdApi(flow_path)
        self.api.RegisterSpi(self)
        self.api.RegisterFront(address)
        self.api.Init()
        self.gateway.write_log(f"MD front connecting: {address}")

    def close(self) -> None:
        if self.api:
            self.api.RegisterSpi(None)
            self.api.Release()
            self.api = None
        self.connect_ok = False
        self.login_ok = False

    def subscribe(self, req: SubscribeRequest) -> None:
        self.subscribed.add(req.symbol)
        if self.login_ok and self.api:
            try:
                symbols = [s.encode("utf-8") for s in [req.symbol]]
                self.api.SubscribeMarketData(symbols, len(symbols))
            except Exception as e:
                self.gateway.write_log(
                    f"MD subscribe error for {req.symbol}: {e}",
                    level=logging.ERROR,
                )

    # ----- SPI 回调（C++ 线程）-----

    def OnFrontConnected(self) -> None:
        self.connect_ok = True
        self.gateway.write_log("MD front connected")
        self._login()

    def OnFrontDisconnected(self, nReason: int) -> None:
        self.connect_ok = False
        self.login_ok = False
        self.gateway.update_connected()
        self.gateway.write_log(
            f"MD front disconnected, reason={nReason}", level=logging.WARNING
        )
        # openctp/CTP 会自动重连；登录将在 OnFrontConnected 中重新触发

    def _login(self) -> None:
        assert self.api is not None
        req = mdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.brokerid
        req.UserID = self.userid
        req.Password = self.password
        self.reqid += 1
        self.api.ReqUserLogin(req, self.reqid)

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID:
            self.gateway.write_log(
                f"MD login failed: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
                level=logging.ERROR,
            )
            return
        self.login_ok = True
        self.gateway.write_log("MD login succeeded")
        self.gateway.update_connected()

        # （重新）订阅到目前为止请求过的全部合约
        if self.subscribed and self.api:
            try:
                symbols = [s.encode("utf-8") for s in self.subscribed]
                self.api.SubscribeMarketData(symbols, len(symbols))
            except Exception as e:
                self.gateway.write_log(
                    f"MD resubscribe error: {e}", level=logging.ERROR
                )

    def OnRspError(self, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID:
            self.gateway.write_log(
                f"MD error: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
                level=logging.ERROR,
            )

    def OnRtnDepthMarketData(self, pDepthMarketData) -> None:
        """将一笔 CTP 深度行情快照转换为 TickData。"""
        data = pDepthMarketData
        symbol = data.InstrumentID

        contract = self.gateway.td_spi.contracts.get(symbol) if self.gateway.td_spi else None
        exchange = contract.exchange if contract else EXCHANGE_MAP.get(data.ExchangeID)
        if not exchange:
            return

        # 时间戳：交易 ActionDay + UpdateTime + 毫秒
        try:
            day = data.ActionDay or datetime.now(CHINA_TZ).strftime("%Y%m%d")
            dt = datetime.strptime(
                f"{day} {data.UpdateTime}", "%Y%m%d %H:%M:%S"
            ).replace(microsecond=data.UpdateMillisec * 1000)
        except ValueError:
            dt = datetime.now(CHINA_TZ).replace(tzinfo=None)

        tick = TickData(
            gateway_name=self.gateway.gateway_name,
            symbol=symbol,
            exchange=exchange,
            datetime=dt,
            name=contract.name if contract else symbol,
            volume=data.Volume,
            turnover=data.Turnover,
            open_interest=data.OpenInterest,
            last_price=adjust_price(data.LastPrice),
            limit_up=adjust_price(data.UpperLimitPrice),
            limit_down=adjust_price(data.LowerLimitPrice),
            open_price=adjust_price(data.OpenPrice),
            high_price=adjust_price(data.HighestPrice),
            low_price=adjust_price(data.LowestPrice),
            pre_close=adjust_price(data.PreClosePrice),
            bid_price_1=adjust_price(data.BidPrice1),
            ask_price_1=adjust_price(data.AskPrice1),
            bid_volume_1=data.BidVolume1,
            ask_volume_1=data.AskVolume1,
        )

        # 第 2-5 档仅在部分柜台上存在
        if data.BidVolume2 or data.AskVolume2:
            tick.bid_price_2 = adjust_price(data.BidPrice2)
            tick.bid_price_3 = adjust_price(data.BidPrice3)
            tick.bid_price_4 = adjust_price(data.BidPrice4)
            tick.bid_price_5 = adjust_price(data.BidPrice5)
            tick.ask_price_2 = adjust_price(data.AskPrice2)
            tick.ask_price_3 = adjust_price(data.AskPrice3)
            tick.ask_price_4 = adjust_price(data.AskPrice4)
            tick.ask_price_5 = adjust_price(data.AskPrice5)
            tick.bid_volume_2 = data.BidVolume2
            tick.bid_volume_3 = data.BidVolume3
            tick.bid_volume_4 = data.BidVolume4
            tick.bid_volume_5 = data.BidVolume5
            tick.ask_volume_2 = data.AskVolume2
            tick.ask_volume_3 = data.AskVolume3
            tick.ask_volume_4 = data.AskVolume4
            tick.ask_volume_5 = data.AskVolume5

        self.gateway.on_tick(tick)


class CtpTdSpi(tdapi.CThostFtdcTraderSpi):
    """交易前置 SPI。"""

    def __init__(
        self,
        gateway: CtpGateway,
        userid: str,
        password: str,
        brokerid: str,
        auth_code: str,
        appid: str,
    ) -> None:
        super().__init__()
        self.gateway = gateway
        self.userid = userid
        self.password = password
        self.brokerid = brokerid
        self.auth_code = auth_code
        self.appid = appid

        self.api: tdapi.CThostFtdcTraderApi | None = None
        self.connect_ok = False
        self.auth_ok = False
        self.login_ok = False
        self.contract_inited = False

        self.reqid = 0
        self.order_ref = 0
        self.frontid = 0
        self.sessionid = 0
        self.trading_day = ""

        # symbol -> ContractData（行情端也用它做交易所反查）
        self.contracts: dict[str, ContractData] = {}
        # orderid -> OrderData 缓存，用于撤单簿记。
        # 由 _orders_lock 保护：REST 线程（send_order）和 CTP 回调线程
        # 会并发写入。
        self.orders: dict[str, OrderData] = {}
        self._orders_lock = threading.Lock()
        # (exchange_id, OrderSysID) -> 本地 orderid，由 OnRtnOrder 维护，
        # 使成交即使跨会话也能映射到正确的订单
        # （等价于 vn.py 的 sysid_orderid_map）。
        self.sysid_orderid_map: dict[tuple[str, str], str] = {}
        # 一轮查询期间的持仓缓冲区：key -> PositionData
        self._position_buffer: dict[str, PositionData] = {}

    # ----- 生命周期 -----

    def connect(self, address: str) -> None:
        if self.api:
            return
        # CTP 流文件（.con）生成到 data/ 目录，避免污染 exe 所在目录
        flow_path = os.path.join(os.getcwd(), "data") + os.sep
        self.api = tdapi.CThostFtdcTraderApi.CreateFtdcTraderApi(flow_path)
        self.api.RegisterSpi(self)
        self.api.SubscribePrivateTopic(tdapi.THOST_TERT_QUICK)
        self.api.SubscribePublicTopic(tdapi.THOST_TERT_QUICK)
        self.api.RegisterFront(address)
        self.api.Init()
        self.gateway.write_log(f"TD front connecting: {address}")

    def close(self) -> None:
        if self.api:
            self.api.RegisterSpi(None)
            self.api.Release()
            self.api = None
        self.connect_ok = False
        self.auth_ok = False
        self.login_ok = False

    # ----- SPI 回调 -----

    def OnFrontConnected(self) -> None:
        self.connect_ok = True
        self.gateway.write_log("TD front connected")
        self._authenticate()

    def OnFrontDisconnected(self, nReason: int) -> None:
        self.connect_ok = False
        self.login_ok = False
        self.gateway.update_connected()
        self.gateway.write_log(
            f"TD front disconnected, reason={nReason}", level=logging.WARNING
        )

    def _authenticate(self) -> None:
        assert self.api is not None
        req = tdapi.CThostFtdcReqAuthenticateField()
        req.BrokerID = self.brokerid
        req.UserID = self.userid
        req.AuthCode = self.auth_code
        req.AppID = self.appid
        self.reqid += 1
        self.api.ReqAuthenticate(req, self.reqid)

    def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID:
            self.gateway.write_log(
                f"TD authenticate failed: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
                level=logging.ERROR,
            )
            return
        self.auth_ok = True
        self.gateway.write_log("TD authenticate succeeded")
        self._login()

    def _login(self) -> None:
        assert self.api is not None
        req = tdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.brokerid
        req.UserID = self.userid
        req.Password = self.password
        self.reqid += 1
        self.api.ReqUserLogin(req, self.reqid)

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID:
            self.gateway.write_log(
                f"TD login failed: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
                level=logging.ERROR,
            )
            return

        self.frontid = pRspUserLogin.FrontID
        self.sessionid = pRspUserLogin.SessionID
        self.trading_day = pRspUserLogin.TradingDay
        self.order_ref = max(self.order_ref, int(pRspUserLogin.MaxOrderRef or 0))
        self.gateway.write_log(
            f"TD login succeeded, trading day: {self.trading_day}"
        )

        # 标记登录成功，使网关被识别为已连接，并让定时器驱动的查询
        # （账户 / 持仓）开始工作。
        self.login_ok = True
        self.gateway.update_connected()

        # 确认结算单，然后查询合约
        assert self.api is not None
        req = tdapi.CThostFtdcSettlementInfoConfirmField()
        req.BrokerID = self.brokerid
        req.InvestorID = self.userid
        self.reqid += 1
        self.api.ReqSettlementInfoConfirm(req, self.reqid)

    def OnRspSettlementInfoConfirm(self, pSettlementInfoConfirm, pRspInfo, nRequestID, bIsLast) -> None:
        self.gateway.write_log("Settlement info confirmed")
        assert self.api is not None
        req = tdapi.CThostFtdcQryInstrumentField()
        self.reqid += 1
        self.api.ReqQryInstrument(req, self.reqid)

    def OnRspQryInstrument(self, pInstrument, pRspInfo, nRequestID, bIsLast) -> None:
        if pInstrument:
            product = PRODUCT_MAP.get(pInstrument.ProductClass)
            exchange = EXCHANGE_MAP.get(pInstrument.ExchangeID)
            if product and exchange:
                contract = ContractData(
                    gateway_name=self.gateway.gateway_name,
                    symbol=pInstrument.InstrumentID,
                    exchange=exchange,
                    name=get_cn_name(
                        pInstrument.InstrumentID,
                        pInstrument.InstrumentName,
                    ),
                    product=product,
                    size=pInstrument.VolumeMultiple,
                    pricetick=pInstrument.PriceTick,
                    min_volume=pInstrument.MinLimitOrderVolume,
                    max_volume=pInstrument.MaxLimitOrderVolume,
                    history_data=True,
                )
                self.contracts[contract.symbol] = contract
                self.gateway.on_contract(contract)

        if bIsLast:
            self.contract_inited = True
            self.gateway.update_connected()
            self.gateway.write_log(
                f"Contract data received: {len(self.contracts)}"
            )
            self.query_account()

    # ----- 查询 -----

    def query_account(self) -> None:
        if not (self.api and self.auth_ok):
            return
        req = tdapi.CThostFtdcQryTradingAccountField()
        req.BrokerID = self.brokerid
        req.InvestorID = self.userid
        self.reqid += 1
        self.api.ReqQryTradingAccount(req, self.reqid)

    def OnRspQryTradingAccount(self, pTradingAccount, pRspInfo, nRequestID, bIsLast) -> None:
        if not pTradingAccount:
            return
        account = AccountData(
            gateway_name=self.gateway.gateway_name,
            accountid=pTradingAccount.AccountID,
            balance=pTradingAccount.Balance,
            frozen=pTradingAccount.FrozenMargin
            + pTradingAccount.FrozenCash
            + pTradingAccount.FrozenCommission,
        )
        self.gateway.on_account(account)

    def query_position(self) -> None:
        if not (self.api and self.auth_ok):
            return
        self._position_buffer.clear()
        req = tdapi.CThostFtdcQryInvestorPositionField()
        req.BrokerID = self.brokerid
        req.InvestorID = self.userid
        self.reqid += 1
        self.api.ReqQryInvestorPosition(req, self.reqid)

    def query_instrument(self, symbol: str) -> None:
        """向 CTP 柜台查询单个合约的信息。"""
        if not (self.api and self.auth_ok):
            return
        req = tdapi.CThostFtdcQryInstrumentField()
        req.InstrumentID = symbol
        self.reqid += 1
        self.api.ReqQryInstrument(req, self.reqid)

    def OnRspQryInvestorPosition(self, pInvestorPosition, pRspInfo, nRequestID, bIsLast) -> None:
        data = pInvestorPosition
        if data:
            contract = self.contracts.get(data.InstrumentID)
            direction = POS_DIRECTION_MAP.get(data.PosiDirection)
            if contract and direction:
                key = f"{data.InstrumentID}.{direction.value}"
                position = self._position_buffer.get(key)
                if not position:
                    position = PositionData(
                        gateway_name=self.gateway.gateway_name,
                        symbol=data.InstrumentID,
                        exchange=contract.exchange,
                        direction=direction,
                    )
                    self._position_buffer[key] = position

                # 聚合多条记录（SHFE/INE 的今仓 / 昨仓）
                position.yd_volume += data.Position - data.TodayPosition

                # 加权平均开仓成本
                size = contract.size or 1
                cost = position.price * position.volume * size
                position.volume += data.Position
                position.pnl += data.PositionProfit
                if position.volume and size:
                    cost += data.PositionCost
                    position.price = cost / (position.volume * size)

                if position.direction == Direction.LONG:
                    position.frozen += data.ShortFrozen
                else:
                    position.frozen += data.LongFrozen

        if bIsLast:
            for position in self._position_buffer.values():
                self.gateway.on_position(position)
            self._position_buffer.clear()

    # ----- 交易 -----

    def send_order(self, req: OrderRequest) -> str:
        if not (self.api and self.login_ok):
            self.gateway.write_log(
                "Send order failed: TD not ready", level=logging.WARNING
            )
            return ""

        if req.type not in ORDERTYPE_MAP:
            self.gateway.write_log(
                f"Unsupported order type: {req.type.value}",
                level=logging.WARNING,
            )
            return ""

        price_type, time_cond, volume_cond = ORDERTYPE_MAP[req.type]

        self.order_ref += 1
        order_ref = str(self.order_ref)

        field = tdapi.CThostFtdcInputOrderField()
        field.BrokerID = self.brokerid
        field.InvestorID = self.userid
        field.UserID = self.userid
        field.InstrumentID = req.symbol
        field.ExchangeID = req.exchange.value
        field.OrderRef = order_ref
        field.Direction = DIRECTION_MAP[req.direction]
        field.CombOffsetFlag = OFFSET_MAP.get(req.offset, tdapi.THOST_FTDC_OF_Open)
        field.CombHedgeFlag = tdapi.THOST_FTDC_HF_Speculation
        field.OrderPriceType = price_type
        field.TimeCondition = time_cond
        field.VolumeCondition = volume_cond
        field.LimitPrice = req.price
        field.VolumeTotalOriginal = int(req.volume)
        field.MinVolume = 1
        field.ContingentCondition = tdapi.THOST_FTDC_CC_Immediately
        field.ForceCloseReason = tdapi.THOST_FTDC_FCC_NotForceClose
        field.IsAutoSuspend = 0

        self.reqid += 1
        n = self.api.ReqOrderInsert(field, self.reqid)
        if n:
            self.gateway.write_log(
                f"ReqOrderInsert failed, code={n}", level=logging.ERROR
            )
            return ""

        orderid = f"{self.frontid}_{self.sessionid}_{order_ref}"
        order = req.create_order_data(orderid, self.gateway.gateway_name)
        order.datetime = datetime.now(CHINA_TZ).replace(tzinfo=None)
        with self._orders_lock:
            self.orders[orderid] = order
        self.gateway.on_order(order)
        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        if not (self.api and self.login_ok):
            return
        try:
            frontid, sessionid, order_ref = req.orderid.split("_")
        except ValueError:
            self.gateway.write_log(
                f"Invalid CTP orderid: {req.orderid}", level=logging.WARNING
            )
            return

        field = tdapi.CThostFtdcInputOrderActionField()
        field.BrokerID = self.brokerid
        field.InvestorID = self.userid
        field.UserID = self.userid
        field.InstrumentID = req.symbol
        field.ExchangeID = req.exchange.value
        field.FrontID = int(frontid)
        field.SessionID = int(sessionid)
        field.OrderRef = order_ref
        field.ActionFlag = tdapi.THOST_FTDC_AF_Delete

        self.reqid += 1
        self.api.ReqOrderAction(field, self.reqid)

    # ----- 订单 / 成交流程 -----

    def OnRspOrderInsert(self, pInputOrder, pRspInfo, nRequestID, bIsLast) -> None:
        """订单被柜台拒绝。"""
        if not (pRspInfo and pRspInfo.ErrorID):
            return
        if pInputOrder:
            orderid = f"{self.frontid}_{self.sessionid}_{pInputOrder.OrderRef}"
            with self._orders_lock:
                order = self.orders.get(orderid)
            if order:
                order.status = Status.REJECTED
                self.gateway.on_order(order)
        self.gateway.write_log(
            f"Order insert rejected: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
            level=logging.WARNING,
        )

    def OnErrRtnOrderInsert(self, pInputOrder, pRspInfo) -> None:
        self.OnRspOrderInsert(pInputOrder, pRspInfo, 0, True)

    def OnRspOrderAction(self, pInputOrderAction, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID:
            self.gateway.write_log(
                f"Order cancel rejected: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
                level=logging.WARNING,
            )

    def OnRtnOrder(self, pOrder) -> None:
        """订单状态更新推送。"""
        data = pOrder
        contract = self.contracts.get(data.InstrumentID)
        exchange = (
            contract.exchange
            if contract
            else EXCHANGE_MAP.get(data.ExchangeID)
        )
        if not exchange:
            return

        orderid = f"{data.FrontID}_{data.SessionID}_{data.OrderRef}"
        status = STATUS_MAP.get(data.OrderStatus, Status.SUBMITTING)

        order_type = ORDERTYPE_MAP_REVERSE.get(
            (data.OrderPriceType, data.TimeCondition, data.VolumeCondition),
            OrderType.LIMIT,
        )

        # CombOffsetFlag 是每腿一个字符的字符串；取第一腿
        offset_char = data.CombOffsetFlag[:1] if data.CombOffsetFlag else ""
        offset = OFFSET_MAP_REVERSE.get(offset_char, Offset.NONE)

        try:
            dt = datetime.strptime(
                f"{data.InsertDate} {data.InsertTime}", "%Y%m%d %H:%M:%S"
            )
        except ValueError:
            dt = datetime.now(CHINA_TZ).replace(tzinfo=None)

        order = OrderData(
            gateway_name=self.gateway.gateway_name,
            symbol=data.InstrumentID,
            exchange=exchange,
            orderid=orderid,
            type=order_type,
            direction=DIRECTION_MAP_REVERSE.get(data.Direction),
            offset=offset,
            price=data.LimitPrice,
            volume=data.VolumeTotalOriginal,
            traded=data.VolumeTraded,
            status=status,
            datetime=dt,
        )
        with self._orders_lock:
            # 保留原订单的 reference（来自策略引擎或 API）
            existing = self.orders.get(orderid)
            if existing and existing.reference:
                order.reference = existing.reference
            self.orders[orderid] = order
            # 记录交易所系统编号，使 OnRtnTrade 能把成交精确映射回
            # 这笔本地订单。
            sysid = (data.OrderSysID or "").strip()
            if sysid:
                self.sysid_orderid_map[(data.ExchangeID, sysid)] = orderid
        self.gateway.on_order(order)

    def OnRtnTrade(self, pTrade) -> None:
        """成交推送。"""
        data = pTrade
        contract = self.contracts.get(data.InstrumentID)
        exchange = (
            contract.exchange
            if contract
            else EXCHANGE_MAP.get(data.ExchangeID)
        )
        if not exchange:
            return

        # 通过交易所 OrderSysID（精确、跨会话安全）把成交映射到对应的
        # 本地订单。仅当订单的 OnRtnOrder 尚未到达时，才回退到
        # OrderRef 后缀扫描。
        sysid = (data.OrderSysID or "").strip()
        with self._orders_lock:
            orderid = self.sysid_orderid_map.get((data.ExchangeID, sysid), "")
            if not orderid:
                for oid in self.orders:
                    if oid.endswith(f"_{data.OrderRef}"):
                        orderid = oid
                        break
        if not orderid:
            orderid = data.OrderRef

        try:
            dt = datetime.strptime(
                f"{data.TradeDate} {data.TradeTime}", "%Y%m%d %H:%M:%S"
            )
        except ValueError:
            dt = datetime.now(CHINA_TZ).replace(tzinfo=None)

        trade = TradeData(
            gateway_name=self.gateway.gateway_name,
            symbol=data.InstrumentID,
            exchange=exchange,
            orderid=orderid,
            tradeid=data.TradeID.strip(),
            direction=DIRECTION_MAP_REVERSE.get(data.Direction),
            offset=OFFSET_MAP_REVERSE.get(data.OffsetFlag),
            price=data.Price,
            volume=data.Volume,
            datetime=dt,
        )
        self.gateway.on_trade(trade)

    def OnRspError(self, pRspInfo, nRequestID, bIsLast) -> None:
        if pRspInfo and pRspInfo.ErrorID:
            self.gateway.write_log(
                f"TD error: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}",
                level=logging.ERROR,
            )
