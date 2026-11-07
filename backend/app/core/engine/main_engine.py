"""主引擎：交易平台的核心。

持有网关 / 功能引擎注册表，并将交易请求路由到对应的网关。
参考 vn.py ``vnpy/trader/engine.py``，做了如下调整：

- 生命周期显式化：``start()`` / ``close()`` 由 FastAPI lifespan
  调用，而不是在构造函数中执行；
- 不使用 ``os.chdir``，去掉 Email/Wechat 引擎；
- 暴露 ``state`` 供 API 健康检查状态使用；
- ``start(loop=...)`` 可透传 asyncio 事件循环给 EventEngine，
  以启用异步处理器派发（供 WebSocket 广播等消费者使用）。
"""

import asyncio
import logging
from typing import TYPE_CHECKING, TypeVar

from ..constant import EngineState, Exchange, Interval
from ..event import EVENT_LOG, Event, EventEngine
from ..gateway.base import BaseGateway
from ..object import (
    BarData,
    CancelRequest,
    HistoryRequest,
    LogData,
    OrderRequest,
    SubscribeRequest,
)
from .base import BaseEngine

if TYPE_CHECKING:
    from .log_engine import LogEngine
    from .oms_engine import OmsEngine

EngineType = TypeVar("EngineType", bound=BaseEngine)


class OrderRejectedError(Exception):
    """当事前风控检查拒绝委托时由 ``MainEngine.send_order`` 抛出。
    携带可读的拒绝原因，使 API 调用方可以展示具体原因
    而不是笼统的失败信息。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MainEngine:
    """作为交易平台的核心。"""

    def __init__(self, event_engine: EventEngine | None = None) -> None:
        self.event_engine: EventEngine = event_engine or EventEngine()

        self.gateways: dict[str, BaseGateway] = {}
        self.engines: dict[str, BaseEngine] = {}
        self.exchanges: list[Exchange] = []

        self.state: EngineState = EngineState.STOPPED

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """启动事件引擎。幂等。

        可选注入 asyncio 事件循环，透传给 EventEngine 以启用
        异步处理器派发（供 WebSocket 广播等消费者使用）。
        """
        if self.state == EngineState.RUNNING:
            return
        self.state = EngineState.STARTING
        self.event_engine.start(loop)
        self.state = EngineState.RUNNING
        self.write_log("MainEngine started")

    def close(self) -> None:
        """先停止事件引擎，再关闭各功能引擎和网关。"""
        if self.state == EngineState.STOPPED:
            return
        self.state = EngineState.STOPPING

        self.event_engine.stop()

        for engine in self.engines.values():
            engine.close()

        for gateway in self.gateways.values():
            gateway.close()

        self.state = EngineState.STOPPED

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def add_engine(self, engine_class: type[EngineType]) -> EngineType:
        """添加功能引擎。"""
        engine: EngineType = engine_class(self, self.event_engine)  # type: ignore[call-arg]
        self.engines[engine.engine_name] = engine
        return engine

    def add_gateway(
        self, gateway_class: type[BaseGateway], gateway_name: str = ""
    ) -> BaseGateway:
        """添加网关。"""
        if not gateway_name:
            gateway_name = gateway_class.default_name

        gateway: BaseGateway = gateway_class(self.event_engine, gateway_name)
        self.gateways[gateway_name] = gateway

        for exchange in gateway.exchanges:
            if exchange not in self.exchanges:
                self.exchanges.append(exchange)

        return gateway

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_gateway(self, gateway_name: str) -> BaseGateway | None:
        gateway: BaseGateway | None = self.gateways.get(gateway_name)
        if not gateway:
            self.write_log(f"Gateway not found: {gateway_name}")
        return gateway

    def get_engine(self, engine_name: str) -> BaseEngine | None:
        return self.engines.get(engine_name)

    def get_default_setting(
        self, gateway_name: str
    ) -> dict[str, str | int | float | bool] | None:
        gateway: BaseGateway | None = self.gateways.get(gateway_name)
        if gateway:
            return gateway.get_default_setting()
        return None

    def get_all_gateway_names(self) -> list[str]:
        return list(self.gateways.keys())

    def get_all_exchanges(self) -> list[Exchange]:
        return self.exchanges

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def write_log(
        self, msg: str, source: str = "MainEngine", level: int = logging.INFO
    ) -> None:
        """推送一条日志事件。"""
        log = LogData(gateway_name=source, msg=msg, level=level)
        self.event_engine.put(Event(EVENT_LOG, log))

    # ------------------------------------------------------------------
    # 交易请求路由
    # ------------------------------------------------------------------

    def connect(self, setting: dict, gateway_name: str) -> None:
        """连接网关。"""
        gateway: BaseGateway | None = self.get_gateway(gateway_name)
        if gateway:
            gateway.connect(setting)

    def subscribe(self, req: SubscribeRequest, gateway_name: str) -> None:
        """通过指定网关订阅行情。"""
        gateway: BaseGateway | None = self.get_gateway(gateway_name)
        if gateway:
            gateway.subscribe(req)

    def send_order(
        self,
        req: OrderRequest,
        gateway_name: str,
        raise_on_reject: bool = False,
    ) -> str:
        """发送委托；返回 vt_orderid（失败/被拒时返回 ''）。

        当 ``raise_on_reject=True`` 时，风控拒绝会抛出携带原因的
        :class:`OrderRejectedError`（供 REST API 使用）；默认保持
        vn.py 风格的 '' 返回值给策略代码使用——策略不应因
        风控拒绝而中断。
        """
        # 事前风控检查（RiskEngine 可选）
        risk = self.engines.get("risk")
        if risk is not None:
            passed, reason = risk.check(req, gateway_name)  # type: ignore[attr-defined]
            if not passed:
                self.write_log(
                    f"risk check rejected order "
                    f"[{req.vt_symbol} {req.direction.value} x{req.volume}]: "
                    f"{reason}",
                    source="RiskEngine",
                    level=logging.WARNING,
                )
                if raise_on_reject:
                    raise OrderRejectedError(f"风控拦截: {reason}")
                return ""

        gateway: BaseGateway | None = self.get_gateway(gateway_name)
        if gateway:
            return gateway.send_order(req)
        if raise_on_reject:
            raise OrderRejectedError(f"网关不存在: {gateway_name}")
        return ""

    def cancel_order(self, req: CancelRequest, gateway_name: str) -> None:
        """撤销委托。"""
        gateway: BaseGateway | None = self.get_gateway(gateway_name)
        if gateway:
            gateway.cancel_order(req)

    def query_history(
        self, req: HistoryRequest, gateway_name: str
    ) -> list[BarData]:
        """通过网关查询历史 K线。

        数据源自动路由：FUYAO（同花顺）已连接且覆盖该交易所时，
        日线请求优先走 FUYAO（前复权高质量数据）；FUYAO 未连接、
        不支持该周期或返回为空时，回退到合约所属网关（如腾讯源）。
        """
        fuyao: BaseGateway | None = self.gateways.get("FUYAO")
        use_fuyao_first = (
            fuyao is not None
            and fuyao.connected
            and req.exchange in fuyao.exchanges
            and req.interval is Interval.DAILY
        )
        if use_fuyao_first:
            bars = fuyao.query_history(req)
            if bars:
                return bars

        gateway: BaseGateway | None = self.get_gateway(gateway_name)
        # 合约网关即 FUYAO 且已优先尝试过时不再重复请求
        if gateway is None or (gateway is fuyao and use_fuyao_first):
            return []
        return gateway.query_history(req)

    # ------------------------------------------------------------------
    # OMS 快捷访问（代理属性，便于使用）
    # ------------------------------------------------------------------

    @property
    def oms(self) -> "OmsEngine":
        from .oms_engine import OmsEngine

        engine = self.engines.get("oms")
        assert isinstance(engine, OmsEngine), "OmsEngine not registered"
        return engine

    @property
    def log(self) -> "LogEngine":
        from .log_engine import LogEngine

        engine = self.engines.get("log")
        assert isinstance(engine, LogEngine), "LogEngine not registered"
        return engine
