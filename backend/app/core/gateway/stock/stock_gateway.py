"""A 股行情网关。

从腾讯行情 API（``qt.gtimg.cn``）拉取报价，该数据源稳定、支持批量
请求，并且包含五档盘口。由于它是拉取式的 HTTP 数据源，由一个轮询
线程按可配置的间隔把快照转换为 TickData 推送（纯行情网关：下单
功能会被礼貌拒绝；真实股票交易需要在后续阶段接入券商网关）。

历史 K 线来自腾讯 K 线 API（``web.ifzq.gtimg.cn``）。
"""

import logging
import threading
import time
from datetime import datetime

import requests

from ...constant import Direction, Exchange, Interval, Product
from ...event import EventEngine
from ...object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderRequest,
    SubscribeRequest,
    TickData,
)
from ..base import BaseGateway

QUOTE_URL = "https://qt.gtimg.cn/q={codes}"
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

INTERVAL_MAP: dict[Interval, str] = {
    Interval.DAILY: "day",
    Interval.WEEKLY: "week",
}


def to_tencent_code(symbol: str, exchange: Exchange) -> str:
    """把 (symbol, exchange) 转换为腾讯代码，如 ``sh600519``。"""
    prefix = {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}.get(exchange.value, "sh")
    return f"{prefix}{symbol}"


def _f(value: str) -> float:
    """安全的浮点数解析。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class StockGateway(BaseGateway):
    """基于腾讯 HTTP API 的 A 股行情网关。"""

    default_name = "STOCK"

    default_setting: dict[str, str | int | float | bool] = {
        "poll_interval": 3.0,
    }

    exchanges: list[Exchange] = [Exchange.SSE, Exchange.SZSE, Exchange.BSE]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)

        self._lock = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None
        self._poll_interval = 3.0

        self._session = requests.Session()
        self._contracts: dict[str, ContractData] = {}   # vt_symbol -> 合约
        self._subscribed: dict[str, str] = {}           # 腾讯代码 -> vt_symbol
        self._last_tick_ts: dict[str, str] = {}         # vt_symbol -> 原始时间戳

    # ------------------------------------------------------------------
    # BaseGateway 接口实现
    # ------------------------------------------------------------------

    def connect(self, setting: dict) -> None:
        if self.connected:
            self.write_log("Stock gateway already connected")
            return

        self._poll_interval = float(
            setting.get("poll_interval", self.default_setting["poll_interval"])
        )

        # 没有内置合约：代码在订阅时懒注册（来自用户自选列表），
        # 名称从真实行情数据中获取。
        self.connected = True
        self.on_gateway_status()
        self.write_log(
            f"Stock gateway connected (Tencent source, "
            f"poll interval {self._poll_interval}s)"
        )
        self.write_log(f"Contract data received: {len(self._contracts)}")

        # 纯行情网关仍然上报一个占位账户
        self.on_account(
            AccountData(
                gateway_name=self.gateway_name,
                accountid="quote_only",
                balance=0,
            )
        )

        self._active = True
        self._thread = threading.Thread(
            target=self._run_poll, name="StockQuotePoll", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        if not self.connected:
            return
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.connected = False
        self.on_gateway_status()
        self.write_log("Stock gateway disconnected")

    def subscribe(self, req: SubscribeRequest) -> None:
        if req.exchange not in self.exchanges:
            self.write_log(
                f"Unsupported exchange for stock gateway: {req.vt_symbol}",
                level=logging.WARNING,
            )
            return

        # 懒注册未知合约，使任意 A 股代码都可用
        if req.vt_symbol not in self._contracts:
            self._register_contract(req.symbol, req.exchange, req.symbol)

        code = to_tencent_code(req.symbol, req.exchange)
        with self._lock:
            self._subscribed[code] = req.vt_symbol
        self.write_log(f"Subscribed market data: {req.vt_symbol}")

    def send_order(self, req: OrderRequest) -> str:
        self.write_log(
            "StockGateway is quote-only; order routing to a stock broker "
            "is planned for a later stage",
            level=logging.WARNING,
        )
        return ""

    def cancel_order(self, req: CancelRequest) -> None:
        self.write_log("StockGateway is quote-only", level=logging.WARNING)

    def query_account(self) -> None:
        return

    def query_position(self) -> None:
        return

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """从腾讯 K 线 API 获取日线 / 周线 K 线。"""
        period = INTERVAL_MAP.get(req.interval or Interval.DAILY)
        if not period:
            self.write_log(
                f"Unsupported history interval: {req.interval}",
                level=logging.WARNING,
            )
            return []

        code = to_tencent_code(req.symbol, req.exchange)
        start = req.start.strftime("%Y-%m-%d")
        end = (req.end or datetime.now()).strftime("%Y-%m-%d")
        param = f"{code},{period},{start},{end},640,qfq"

        try:
            resp = self._session.get(
                KLINE_URL, params={"param": param}, timeout=10
            )
            payload = resp.json()
        except Exception as e:
            self.write_log(
                f"History query failed: {e}", level=logging.ERROR
            )
            return []

        data = payload.get("data", {}).get(code, {})
        rows = data.get(f"qfq{period}") or data.get(period) or []

        bars: list[BarData] = []
        for row in rows:
            # [日期, 开盘, 收盘, 最高, 最低, 成交量, ...]
            try:
                dt = datetime.strptime(row[0], "%Y-%m-%d")
            except (ValueError, IndexError):
                continue
            bars.append(
                BarData(
                    gateway_name=self.gateway_name,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=dt,
                    interval=req.interval,
                    open_price=_f(row[1]),
                    close_price=_f(row[2]),
                    high_price=_f(row[3]),
                    low_price=_f(row[4]),
                    volume=_f(row[5]),
                )
            )
        return bars

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _register_contract(
        self, symbol: str, exchange: Exchange, name: str
    ) -> None:
        product = Product.ETF if symbol.startswith(("5", "1")) else Product.EQUITY
        contract = ContractData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            name=name,
            product=product,
            size=1,
            pricetick=0.01,
            min_volume=100,  # A 股一手
            history_data=True,
        )
        self._contracts[contract.vt_symbol] = contract
        self.on_contract(contract)

    def _run_poll(self) -> None:
        """轮询已订阅的代码并推送 Tick行情。"""
        while self._active:
            time.sleep(self._poll_interval)
            if not self._active:
                break

            with self._lock:
                codes = list(self._subscribed.keys())
            if not codes:
                continue

            try:
                self._poll_once(codes)
            except Exception as e:
                self.write_log(
                    f"Quote poll error: {e}", level=logging.WARNING
                )

    def _poll_once(self, codes: list[str]) -> None:
        """一次批量报价请求；腾讯支持单次请求多个代码。"""
        url = QUOTE_URL.format(codes=",".join(codes))
        resp = self._session.get(url, timeout=10)
        resp.encoding = "gbk"

        for line in resp.text.strip().split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            var, _, payload = line.partition("=")
            code = var.split("_")[-1]
            vt_symbol = self._subscribed.get(code)
            if not vt_symbol:
                continue
            contract = self._contracts.get(vt_symbol)
            if not contract:
                continue

            fields = payload.strip('"').split("~")
            if len(fields) < 49:
                continue

            tick = self._parse_quote(fields, contract)
            if tick is None:
                continue

            # 时间戳未前进（休市 / 无成交）则跳过
            raw_ts = fields[30]
            if self._last_tick_ts.get(vt_symbol) == raw_ts:
                continue
            self._last_tick_ts[vt_symbol] = raw_ts

            # 首次见到时用行情数据更新合约名称
            if contract.name == contract.symbol and fields[1]:
                self._register_contract(
                    contract.symbol, contract.exchange, fields[1]
                )

            self.on_tick(tick)

    def _parse_quote(
        self, fields: list[str], contract: ContractData
    ) -> TickData | None:
        """把腾讯报价负载（``~`` 分隔）解析为 TickData。

        字段布局：1 名称，3 最新价，4 昨收，5 开盘，6 成交量（手），
        9..18 买一价 / 量 .. 买五，19..28 卖一价 / 量 .. 卖五，
        30 时间戳，33 最高，34 最低，37 成交额（万元），47/48 涨停 / 跌停。
        """
        try:
            dt = datetime.strptime(fields[30], "%Y%m%d%H%M%S")
        except ValueError:
            return None

        tick = TickData(
            gateway_name=self.gateway_name,
            symbol=contract.symbol,
            exchange=contract.exchange,
            datetime=dt,
            name=fields[1] or contract.name,
            last_price=_f(fields[3]),
            pre_close=_f(fields[4]),
            open_price=_f(fields[5]),
            volume=_f(fields[6]) * 100,       # 手 -> 股
            turnover=_f(fields[37]) * 10000,  # 万元 -> 元
            high_price=_f(fields[33]),
            low_price=_f(fields[34]),
            limit_up=_f(fields[47]),
            limit_down=_f(fields[48]),
        )

        for level in range(5):
            setattr(tick, f"bid_price_{level + 1}", _f(fields[9 + level * 2]))
            setattr(
                tick,
                f"bid_volume_{level + 1}",
                _f(fields[10 + level * 2]) * 100,
            )
            setattr(tick, f"ask_price_{level + 1}", _f(fields[19 + level * 2]))
            setattr(
                tick,
                f"ask_volume_{level + 1}",
                _f(fields[20 + level * 2]) * 100,
            )
        return tick
