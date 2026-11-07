"""策略模板与本地停止单定义。

策略子类需要声明：
- ``parameters``：可配置的类属性名称列表（默认值即类属性的取值）；
- ``variables``：暴露给 UI 的运行时状态属性名称列表。

生命周期钩子：on_init / on_start / on_stop / on_tick / on_bar /
on_trade / on_order / on_stop_order。
交易动作：buy / sell / short / cover（可选传入 ``stop=True``
以发出本地停止单）。

模板通过鸭子类型接口与引擎交互，该接口由 StrategyEngine（实盘）
和 BacktestingEngine（回测）共同实现：
send_order / cancel_order / cancel_all / load_bar / write_strategy_log /
put_strategy_event。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..constant import Direction, Interval, Offset
from ..object import BarData, OrderData, TickData, TradeData


class StopOrderStatus(Enum):
    WAITING = "WAITING"
    CANCELLED = "CANCELLED"
    TRIGGERED = "TRIGGERED"


STOPORDER_PREFIX = "STOP"


@dataclass
class StopOrder:
    """由引擎持有、触发前不发往柜台的本地停止单。"""

    vt_symbol: str
    direction: Direction
    offset: Offset
    price: float
    volume: float
    stop_orderid: str
    strategy_name: str
    datetime: datetime
    status: StopOrderStatus = StopOrderStatus.WAITING
    vt_orderids: list[str] = field(default_factory=list)


class StrategyTemplate:
    """所有交易策略的基类。"""

    author: str = ""
    # 展示在 UI 上的中文策略名（为空时回退到类名）
    display_name: str = ""
    # 展示在 UI 上的人类可读策略描述
    description: str = ""
    parameters: list[str] = []
    variables: list[str] = []
    # 可选：供 UI 展示的逐参数 / 逐变量说明
    param_descriptions: dict[str, str] = {}
    variable_descriptions: dict[str, str] = {}

    def __init__(
        self,
        strategy_engine: Any,
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ) -> None:
        self.strategy_engine = strategy_engine
        self.strategy_name: str = strategy_name
        self.vt_symbol: str = vt_symbol

        self.inited: bool = False
        self.trading: bool = False
        self.pos: float = 0.0

        self.update_setting(setting)

    # ------------------------------------------------------------------
    # 生命周期钩子（在子类中重写）
    # ------------------------------------------------------------------

    def on_init(self) -> None:
        """策略初始化时调用。"""

    def on_start(self) -> None:
        """策略启动交易时调用。"""

    def on_stop(self) -> None:
        """策略停止交易时调用。"""

    def on_tick(self, tick: TickData) -> None:
        """收到新 Tick 行情时调用。"""

    def on_bar(self, bar: BarData) -> None:
        """收到新 K线 数据时调用。"""

    def on_trade(self, trade: TradeData) -> None:
        """收到新成交时调用（引擎已先更新过 pos）。"""

    def on_order(self, order: OrderData) -> None:
        """收到委托状态更新时调用。"""

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """收到本地停止单状态更新时调用。"""

    # ------------------------------------------------------------------
    # 交易动作
    # ------------------------------------------------------------------

    def buy(self, price: float, volume: float, stop: bool = False) -> list[str]:
        """买入开多。"""
        return self.send_order(Direction.LONG, Offset.OPEN, price, volume, stop)

    def sell(self, price: float, volume: float, stop: bool = False) -> list[str]:
        """卖出平多。"""
        return self.send_order(Direction.SHORT, Offset.CLOSE, price, volume, stop)

    def short(self, price: float, volume: float, stop: bool = False) -> list[str]:
        """卖出开空。"""
        return self.send_order(Direction.SHORT, Offset.OPEN, price, volume, stop)

    def cover(self, price: float, volume: float, stop: bool = False) -> list[str]:
        """买入平空。"""
        return self.send_order(Direction.LONG, Offset.CLOSE, price, volume, stop)

    def send_order(
        self,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        stop: bool = False,
    ) -> list[str]:
        """通过引擎发送委托；未处于交易状态时不执行任何操作。"""
        if not self.trading:
            return []
        return self.strategy_engine.send_order(
            self, direction, offset, price, volume, stop
        )

    def cancel_order(self, vt_orderid: str) -> None:
        if self.trading:
            self.strategy_engine.cancel_order(self, vt_orderid)

    def cancel_all(self) -> None:
        if self.trading:
            self.strategy_engine.cancel_all(self)

    # ------------------------------------------------------------------
    # 数据与工具方法
    # ------------------------------------------------------------------

    def load_bar(
        self,
        days: int,
        interval: Interval = Interval.MINUTE,
        callback: Any = None,
    ) -> None:
        """加载历史 K线 用于预热（默认路由到 on_bar）。"""
        self.strategy_engine.load_bar(
            self.vt_symbol, days, interval, callback or self.on_bar
        )

    def write_log(self, msg: str) -> None:
        self.strategy_engine.write_strategy_log(self, msg)

    def put_event(self) -> None:
        """把最新的策略状态推送到 UI。"""
        self.strategy_engine.put_strategy_event(self)

    # ------------------------------------------------------------------
    # 参数与状态
    # ------------------------------------------------------------------

    def update_setting(self, setting: dict) -> None:
        """仅对已声明的参数应用配置值。"""
        for name in self.parameters:
            if name in setting:
                setattr(self, name, setting[name])

    @classmethod
    def get_class_parameters(cls) -> dict:
        """参数名称及其类级默认值。"""
        return {name: getattr(cls, name, None) for name in cls.parameters}

    def get_parameters(self) -> dict:
        return {name: getattr(self, name, None) for name in self.parameters}

    def get_variables(self) -> dict:
        data = {
            "inited": self.inited,
            "trading": self.trading,
            "pos": self.pos,
        }
        for name in self.variables:
            data[name] = getattr(self, name, None)
        return data

    def get_data(self) -> dict:
        """供 API/WS 使用的完整状态快照。"""
        return {
            "strategy_name": self.strategy_name,
            "class_name": type(self).__name__,
            "vt_symbol": self.vt_symbol,
            "author": self.author,
            "parameters": self.get_parameters(),
            "variables": self.get_variables(),
            "auto_start": getattr(self, "auto_start", False),
        }
