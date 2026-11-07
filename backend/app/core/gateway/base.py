"""抽象网关接口。

参考 vn.py 的 ``vnpy/trader/gateway.py`` 设计。网关实现必须满足：

- 线程安全（回调可能来自任意线程）；
- 非阻塞（所有方法立即返回）；
- 连接断开时自动重连。

所有 ``on_xxx`` 回调采用双主题模式推送事件：通用主题（如 ``eTick.``）
加上追加了对象 ID 的具体主题（如 ``eTick.IF2509.CFFEX``）。
"""

from abc import ABC, abstractmethod

from ..event import (
    EVENT_ACCOUNT,
    EVENT_CONTRACT,
    EVENT_GATEWAY_STATUS,
    EVENT_LOG,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from ..object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    LogData,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)
from ..constant import Exchange


class BaseGateway(ABC):
    """用于连接交易系统的抽象网关。"""

    # 网关的默认名称。
    default_name: str = ""

    # connect() 所需的 setting 字典字段；同时作为前端渲染连接表单的
    # schema 使用。
    default_setting: dict[str, str | int | float | bool] = {}

    # 该网关支持的交易所。
    exchanges: list[Exchange] = []

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        self.event_engine: EventEngine = event_engine
        self.gateway_name: str = gateway_name
        self.connected: bool = False

    # ------------------------------------------------------------------
    # 事件推送回调
    # ------------------------------------------------------------------

    def on_event(self, type: str, data: object = None) -> None:
        """通用事件推送。"""
        self.event_engine.put(Event(type, data))

    def on_tick(self, tick: TickData) -> None:
        """Tick行情事件推送（通用主题 + 按合约主题）。"""
        self.on_event(EVENT_TICK, tick)
        self.on_event(EVENT_TICK + tick.vt_symbol, tick)

    def on_trade(self, trade: TradeData) -> None:
        """成交事件推送（通用主题 + 按合约主题）。"""
        self.on_event(EVENT_TRADE, trade)
        self.on_event(EVENT_TRADE + trade.vt_symbol, trade)

    def on_order(self, order: OrderData) -> None:
        """订单事件推送（通用主题 + 按订单号主题）。"""
        self.on_event(EVENT_ORDER, order)
        self.on_event(EVENT_ORDER + order.vt_orderid, order)

    def on_position(self, position: PositionData) -> None:
        """持仓事件推送（通用主题 + 按合约主题）。"""
        self.on_event(EVENT_POSITION, position)
        self.on_event(EVENT_POSITION + position.vt_symbol, position)

    def on_account(self, account: AccountData) -> None:
        """账户事件推送（通用主题 + 按账户号主题）。"""
        self.on_event(EVENT_ACCOUNT, account)
        self.on_event(EVENT_ACCOUNT + account.vt_accountid, account)

    def on_contract(self, contract: ContractData) -> None:
        """合约事件推送。"""
        self.on_event(EVENT_CONTRACT, contract)

    def on_log(self, log: LogData) -> None:
        """日志事件推送。"""
        self.on_event(EVENT_LOG, log)

    def on_gateway_status(self) -> None:
        """连接状态事件推送（结构化数据，供 Web 客户端使用）。"""
        self.on_event(
            EVENT_GATEWAY_STATUS,
            {"gateway_name": self.gateway_name, "connected": self.connected},
        )

    def write_log(self, msg: str, level: int = 20) -> None:
        """通过事件写入一条日志消息。"""
        log = LogData(gateway_name=self.gateway_name, msg=msg, level=level)
        self.on_log(log)

    # ------------------------------------------------------------------
    # 抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self, setting: dict) -> None:
        """
        启动连接。连接成功后，网关必须通过 on_xxx 回调查询并推送
        合约 / 账户 / 持仓 / 订单 / 成交数据。
        """

    @abstractmethod
    def close(self) -> None:
        """关闭连接。"""

    @abstractmethod
    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅合约行情。"""

    @abstractmethod
    def send_order(self, req: OrderRequest) -> str:
        """
        发送新订单。实现必须：

        1. 在本地或从服务器创建 orderid；
        2. 立即通过 on_order 推送初始 OrderData（SUBMITTING 或
           REJECTED 状态）；
        3. 返回 vt_orderid。
        """

    @abstractmethod
    def cancel_order(self, req: CancelRequest) -> None:
        """撤销已有订单。"""

    @abstractmethod
    def query_account(self) -> None:
        """查询账户资金；结果通过 on_account 返回。"""

    @abstractmethod
    def query_position(self) -> None:
        """查询持仓；结果通过 on_position 返回。"""

    # ------------------------------------------------------------------
    # 可选方法
    # ------------------------------------------------------------------

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """查询历史 K 线数据（同步）。"""
        return []

    def get_default_setting(self) -> dict[str, str | int | float | bool]:
        """返回默认连接配置 schema。"""
        return self.default_setting
