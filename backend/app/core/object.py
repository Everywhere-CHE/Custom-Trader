"""交易平台的领域数据对象。

参考 vn.py ``vnpy/trader/object.py``。复合 ID 约定：

- ``vt_symbol = f"{symbol}.{exchange.value}"``
- ``vt_orderid = f"{gateway_name}.{orderid}"``
- ``vt_tradeid = f"{gateway_name}.{tradeid}"``
- ``vt_positionid = f"{gateway_name}.{vt_symbol}.{direction.value}"``
- ``vt_accountid = f"{gateway_name}.{accountid}"``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .constant import (
    ACTIVE_STATUSES,
    Direction,
    Exchange,
    Interval,
    Offset,
    OrderType,
    Product,
    Status,
)


@dataclass
class BaseData:
    """所有数据对象的基类；携带来源网关名称。"""

    gateway_name: str

    extra: dict | None = field(default=None, init=False)


@dataclass
class TickData(BaseData):
    """Tick 行情数据：包含五档盘口的最新市场快照。"""

    symbol: str = ""
    exchange: Exchange = Exchange.LOCAL
    datetime: datetime | None = None

    name: str = ""
    volume: float = 0
    turnover: float = 0
    open_interest: float = 0
    last_price: float = 0
    last_volume: float = 0
    limit_up: float = 0
    limit_down: float = 0

    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    pre_close: float = 0

    bid_price_1: float = 0
    bid_price_2: float = 0
    bid_price_3: float = 0
    bid_price_4: float = 0
    bid_price_5: float = 0

    ask_price_1: float = 0
    ask_price_2: float = 0
    ask_price_3: float = 0
    ask_price_4: float = 0
    ask_price_5: float = 0

    bid_volume_1: float = 0
    bid_volume_2: float = 0
    bid_volume_3: float = 0
    bid_volume_4: float = 0
    bid_volume_5: float = 0

    ask_volume_1: float = 0
    ask_volume_2: float = 0
    ask_volume_3: float = 0
    ask_volume_4: float = 0
    ask_volume_5: float = 0

    localtime: datetime | None = None

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"


@dataclass
class BarData(BaseData):
    """K线（蜡烛图）数据。"""

    symbol: str = ""
    exchange: Exchange = Exchange.LOCAL
    datetime: datetime | None = None

    interval: Interval | None = None
    volume: float = 0
    turnover: float = 0
    open_interest: float = 0
    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    close_price: float = 0

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"


@dataclass
class OrderData(BaseData):
    """委托的最新状态。"""

    symbol: str = ""
    exchange: Exchange = Exchange.LOCAL
    orderid: str = ""

    type: OrderType = OrderType.LIMIT
    direction: Direction | None = None
    offset: Offset = Offset.NONE
    price: float = 0
    volume: float = 0
    traded: float = 0
    status: Status = Status.SUBMITTING
    datetime: datetime | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"
        self.vt_orderid: str = f"{self.gateway_name}.{self.orderid}"

    def is_active(self) -> bool:
        """委托是否仍处于活动状态（可能被成交/撤销）。"""
        return self.status in ACTIVE_STATUSES

    def create_cancel_request(self) -> "CancelRequest":
        """根据此委托创建一个撤单请求。"""
        return CancelRequest(
            orderid=self.orderid,
            symbol=self.symbol,
            exchange=self.exchange,
        )


@dataclass
class TradeData(BaseData):
    """委托的成交（撮合）数据。"""

    symbol: str = ""
    exchange: Exchange = Exchange.LOCAL
    orderid: str = ""
    tradeid: str = ""
    direction: Direction | None = None

    offset: Offset = Offset.NONE
    price: float = 0
    volume: float = 0
    datetime: datetime | None = None

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"
        self.vt_orderid: str = f"{self.gateway_name}.{self.orderid}"
        self.vt_tradeid: str = f"{self.gateway_name}.{self.tradeid}"


@dataclass
class PositionData(BaseData):
    """某标的在某一方向上的持仓。"""

    symbol: str = ""
    exchange: Exchange = Exchange.LOCAL
    direction: Direction = Direction.NET

    volume: float = 0
    frozen: float = 0
    price: float = 0
    pnl: float = 0
    yd_volume: float = 0
    datetime: datetime | None = None  # 持仓开仓时间

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"
        self.vt_positionid: str = (
            f"{self.gateway_name}.{self.vt_symbol}.{self.direction.value}"
        )


@dataclass
class AccountData(BaseData):
    """账户资金数据。"""

    accountid: str = ""

    balance: float = 0
    frozen: float = 0

    def __post_init__(self) -> None:
        self.available: float = self.balance - self.frozen
        self.vt_accountid: str = f"{self.gateway_name}.{self.accountid}"


@dataclass
class LogData(BaseData):
    """日志消息数据。"""

    msg: str = ""
    level: int = 20  # logging.INFO

    def __post_init__(self) -> None:
        self.time: datetime = datetime.now()


@dataclass
class ContractData(BaseData):
    """合约（交易品种）定义。"""

    symbol: str = ""
    exchange: Exchange = Exchange.LOCAL
    name: str = ""
    product: Product = Product.FUTURES
    size: float = 1
    pricetick: float = 0

    min_volume: float = 1
    max_volume: float | None = None
    stop_supported: bool = False
    net_position: bool = False
    history_data: bool = False

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"


@dataclass
class SubscribeRequest:
    """订阅某合约行情的请求。"""

    symbol: str
    exchange: Exchange

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"


@dataclass
class OrderRequest:
    """发送新委托的请求。"""

    symbol: str
    exchange: Exchange
    direction: Direction
    type: OrderType
    volume: float
    price: float = 0
    offset: Offset = Offset.NONE
    reference: str = ""

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"

    def create_order_data(self, orderid: str, gateway_name: str) -> OrderData:
        """根据此请求创建 OrderData。"""
        return OrderData(
            gateway_name=gateway_name,
            symbol=self.symbol,
            exchange=self.exchange,
            orderid=orderid,
            type=self.type,
            direction=self.direction,
            offset=self.offset,
            price=self.price,
            volume=self.volume,
            reference=self.reference,
        )


@dataclass
class CancelRequest:
    """撤销已有委托的请求。"""

    orderid: str
    symbol: str
    exchange: Exchange

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"


@dataclass
class HistoryRequest:
    """查询历史 K线数据的请求。"""

    symbol: str
    exchange: Exchange
    start: datetime
    end: datetime | None = None
    interval: Interval | None = None

    def __post_init__(self) -> None:
        self.vt_symbol: str = f"{self.symbol}.{self.exchange.value}"
