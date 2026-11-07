"""订单管理系统引擎：在内存中缓存最新的平台状态
（Tick 行情 / 委托 / 成交 / 持仓 / 账户 / 合约），
作为 REST 查询的实时数据来源。"""

from datetime import datetime
from typing import TYPE_CHECKING

from ..constant import Direction, EngineState, Exchange
from ..converter import OffsetConverter
from ..event import (
    EVENT_ACCOUNT,
    EVENT_CONTRACT,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from ..object import (
    AccountData,
    ContractData,
    OrderData,
    OrderRequest,
    PositionData,
    TickData,
    TradeData,
)
from ..utility import CHINA_TZ
from .base import BaseEngine

if TYPE_CHECKING:
    from .main_engine import MainEngine


class OmsEngine(BaseEngine):
    """提供订单管理系统功能。"""

    def __init__(
        self,
        main_engine: "MainEngine",
        event_engine: EventEngine,
    ) -> None:
        super().__init__(main_engine, event_engine, "oms")

        self.ticks: dict[str, TickData] = {}
        self.orders: dict[str, OrderData] = {}
        self.trades: dict[str, TradeData] = {}
        self.positions: dict[str, PositionData] = {}
        self.accounts: dict[str, AccountData] = {}
        self.contracts: dict[str, ContractData] = {}

        self.active_orders: dict[str, OrderData] = {}

        self.offset_converters: dict[str, OffsetConverter] = {}

        self.register_event()

    def register_event(self) -> None:
        """注册事件处理器。"""
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_POSITION, self.process_position_event)
        self.event_engine.register(EVENT_ACCOUNT, self.process_account_event)
        self.event_engine.register(EVENT_CONTRACT, self.process_contract_event)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    def process_tick_event(self, event: Event) -> None:
        tick: TickData = event.data
        self.ticks[tick.vt_symbol] = tick

    def process_order_event(self, event: Event) -> None:
        order: OrderData = event.data
        self.orders[order.vt_orderid] = order

        # 维护活动委托子集
        if order.is_active():
            self.active_orders[order.vt_orderid] = order
        elif order.vt_orderid in self.active_orders:
            self.active_orders.pop(order.vt_orderid)

        converter: OffsetConverter | None = self.offset_converters.get(
            order.gateway_name
        )
        if converter:
            converter.update_order(order)

    def process_trade_event(self, event: Event) -> None:
        trade: TradeData = event.data
        self.trades[trade.vt_tradeid] = trade

        converter: OffsetConverter | None = self.offset_converters.get(
            trade.gateway_name
        )
        if converter:
            converter.update_trade(trade)

    def process_position_event(self, event: Event) -> None:
        position: PositionData = event.data
        # 记录持仓首次开仓的时间
        if position.volume > 0 and position.vt_positionid not in self.positions:
            position.datetime = datetime.now(CHINA_TZ)
        # 用当前 tick 重算浮动盈亏（弥补 CTP 不返回 PositionProfit 的情况）
        self._recalc_position_pnl(position)
        self.positions[position.vt_positionid] = position

        converter: OffsetConverter | None = self.offset_converters.get(
            position.gateway_name
        )
        if converter:
            converter.update_position(position)

    def _recalc_position_pnl(self, position: PositionData) -> None:
        """根据当前 tick 价格重算持仓浮动盈亏。"""
        if position.volume <= 0:
            position.pnl = 0.0
            return
        tick = self.ticks.get(position.vt_symbol)
        contract = self.contracts.get(position.vt_symbol)
        if not tick or not tick.last_price:
            return  # 无行情，保留原值
        size = contract.size if contract and contract.size else 1
        if position.direction == Direction.LONG:
            position.pnl = round(
                (tick.last_price - position.price) * position.volume * size, 2
            )
        else:
            position.pnl = round(
                (position.price - tick.last_price) * position.volume * size, 2
            )

    def process_account_event(self, event: Event) -> None:
        account: AccountData = event.data
        self.accounts[account.vt_accountid] = account

    def process_contract_event(self, event: Event) -> None:
        contract: ContractData = event.data
        self.contracts[contract.vt_symbol] = contract

        # 为每个网关按需初始化一个开平转换器
        if contract.gateway_name not in self.offset_converters:
            self.offset_converters[contract.gateway_name] = OffsetConverter(
                contract.gateway_name
            )
        self.offset_converters[contract.gateway_name].update_contract(contract)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_tick(self, vt_symbol: str) -> TickData | None:
        return self.ticks.get(vt_symbol)

    def get_order(self, vt_orderid: str) -> OrderData | None:
        return self.orders.get(vt_orderid)

    def get_trade(self, vt_tradeid: str) -> TradeData | None:
        return self.trades.get(vt_tradeid)

    def get_position(self, vt_positionid: str) -> PositionData | None:
        return self.positions.get(vt_positionid)

    def get_account(self, vt_accountid: str) -> AccountData | None:
        return self.accounts.get(vt_accountid)

    def get_contract(self, vt_symbol: str) -> ContractData | None:
        return self.contracts.get(vt_symbol)

    def get_all_ticks(self) -> list[TickData]:
        return list(self.ticks.values())

    def get_all_orders(self) -> list[OrderData]:
        return list(self.orders.values())

    def get_all_trades(self) -> list[TradeData]:
        return list(self.trades.values())

    def get_all_positions(self) -> list[PositionData]:
        return list(self.positions.values())

    def get_all_accounts(self) -> list[AccountData]:
        return list(self.accounts.values())

    def get_all_contracts(self) -> list[ContractData]:
        return list(self.contracts.values())

    def get_all_active_orders(self, vt_symbol: str = "") -> list[OrderData]:
        """获取所有活动委托，可按 vt_symbol 过滤。"""
        if not vt_symbol:
            return list(self.active_orders.values())
        return [
            order
            for order in self.active_orders.values()
            if order.vt_symbol == vt_symbol
        ]

    def clear_orders(self) -> int:
        """清空内存中的所有委托（不影响数据库）。"""
        count = len(self.orders) + len(self.active_orders)
        self.orders.clear()
        self.active_orders.clear()
        return count

    def clear_trades(self) -> int:
        """清空内存中的所有成交（不影响数据库）。"""
        count = len(self.trades)
        self.trades.clear()
        return count

    # ------------------------------------------------------------------
    # 开平转换（第一阶段为直通）
    # ------------------------------------------------------------------

    def update_order_request(
        self, req: OrderRequest, vt_orderid: str, gateway_name: str
    ) -> None:
        converter: OffsetConverter | None = self.offset_converters.get(gateway_name)
        if converter:
            converter.update_order_request(req, vt_orderid)

    def convert_order_request(
        self,
        req: OrderRequest,
        gateway_name: str,
        lock: bool = False,
        net: bool = False,
    ) -> list[OrderRequest]:
        converter: OffsetConverter | None = self.offset_converters.get(gateway_name)
        if not converter:
            return [req]
        return converter.convert_order_request(req, lock, net)

    def get_converter(self, gateway_name: str) -> OffsetConverter | None:
        return self.offset_converters.get(gateway_name)
