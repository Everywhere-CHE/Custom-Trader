"""从（基于线程的）EventEngine 桥接到 asyncio 世界。

该桥接向 EventEngine 注册**异步**处理器（``register_async``）；
EventEngine 在注入的事件循环上原生调度本协程，序列化事件负载
并投入有界 asyncio 队列，单个广播任务从队列取出消息分发给各
WebSocket 客户端。

改用 ``register_async`` 后，本桥接不再自行
``call_soon_threadsafe`` 跨线程投递——线程→循环的桥接已内建在
EventEngine 中。有界队列与背压策略保留：

背压：当队列已满时，tick 消息会被丢弃（行情数据是快照式的，
以最新为准）；其他消息会挤出最旧的 tick，或作为最后手段被丢弃。
丢弃计数会用于 /health。
"""

import asyncio

from ...core.event import (
    EVENT_ACCOUNT,
    EVENT_GATEWAY_STATUS,
    EVENT_LOG,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from ...core.event.type import EVENT_STRATEGY, EVENT_STRATEGY_LOG
from ..schemas import (
    serialize_account,
    serialize_log,
    serialize_order,
    serialize_position,
    serialize_tick,
    serialize_trade,
)
from .manager import ConnectionManager

# 事件类型 -> (ws 消息类型, 序列化器)
_SERIALIZERS = {
    EVENT_TICK: ("tick", serialize_tick),
    EVENT_ORDER: ("order", serialize_order),
    EVENT_TRADE: ("trade", serialize_trade),
    EVENT_POSITION: ("position", serialize_position),
    EVENT_ACCOUNT: ("account", serialize_account),
    EVENT_LOG: ("log", serialize_log),
    EVENT_GATEWAY_STATUS: ("gateway_status", lambda d: d),
    EVENT_STRATEGY: ("strategy", lambda d: d),
    EVENT_STRATEGY_LOG: ("strategy_log", lambda d: d),
}


class EventBridge:
    """单向桥接：事件线程 -> asyncio 循环。"""

    def __init__(self, maxsize: int = 10000) -> None:
        self._maxsize = maxsize
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self.dropped_count: int = 0

    def start(
        self,
        event_engine: EventEngine,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """注册异步处理器；建议 EventEngine 已注入事件循环。

        ``loop`` 参数保留以兼容旧调用方（若传入则记录供调试）；
        实际的跨线程投递由 EventEngine 经 ``register_async`` 完成。
        """
        if loop is not None:
            self._loop = loop
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        for event_type in _SERIALIZERS:
            event_engine.register_async(event_type, self._on_event_async)

    async def _on_event_async(self, event: Event) -> None:
        """在事件循环上运行（由 EventEngine 原生异步派发调度）。

        序列化事件负载并投入有界队列；背压策略见 :meth:`_enqueue`。
        相比旧实现，省去了分发线程内的 ``call_soon_threadsafe`` 手动
        跨线程投递——EventEngine 已在循环上调度本协程。
        """
        if self._queue is None:
            return

        entry = _SERIALIZERS.get(event.type)
        if entry is None:
            return
        msg_type, serializer = entry
        message = {"type": msg_type, "data": serializer(event.data)}
        self._enqueue(message)

    def _enqueue(self, message: dict) -> None:
        """在事件循环中运行；应用背压策略。"""
        assert self._queue is not None
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            if message["type"] == "tick":
                self.dropped_count += 1
                return
            # 挤出最旧的消息，为关键更新腾出空间
            try:
                self._queue.get_nowait()
                self.dropped_count += 1
                self._queue.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                self.dropped_count += 1

    async def broadcaster(self, manager: ConnectionManager) -> None:
        """取出队列消息并广播；作为 asyncio 任务运行。"""
        assert self._queue is not None
        while True:
            message = await self._queue.get()
            await manager.broadcast(message)
