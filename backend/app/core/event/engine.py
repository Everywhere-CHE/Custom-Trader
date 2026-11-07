"""事件驱动框架核心。

基于线程的设计（queue.Queue + 分发线程 + 定时器线程），
参考 vn.py ``vnpy/event/engine.py``。必须采用基于线程的引擎，
因为网关回调（例如第二阶段的 CTP C++ API）会从任意
非 asyncio 线程到达。

相对 vn.py 的改进：
- 捕获处理器异常，避免单个异常处理器导致分发线程崩溃；
- ``stop()`` 是幂等的；
- **原生异步派发**：``start(loop=...)`` 注入 asyncio 循环后，经
  ``register_async`` 注册的异步处理器会在该循环上调度
  （``call_soon_threadsafe`` + ``create_task``）。这样需要进入
  asyncio 世界的消费者（如 WebSocket 广播）不必再各自外挂
  线程→循环桥接——但同步处理器路径完全不变，未注入 loop 时
  异步处理器静默不触发，向后兼容。
"""

import asyncio
import sys
import traceback
from collections import defaultdict
from collections.abc import Awaitable, Callable
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any

from .type import EVENT_TIMER


class Event:
    """事件对象，包含用于路由的类型字符串和数据负载。"""

    def __init__(self, type: str, data: Any = None) -> None:
        self.type: str = type
        self.data: Any = data
        # 入队时刻（monotonic 秒），供分发延迟统计；由 EventEngine.put 写入
        self.enqueued_at: float = 0.0


HandlerType = Callable[[Event], None]
AsyncHandlerType = Callable[[Event], Awaitable[None]]


class EventEngine:
    """
    将事件分发给已注册的处理器，并每隔 ``interval`` 秒
    生成一个定时器事件。
    """

    def __init__(self, interval: float = 1.0) -> None:
        self._interval: float = interval
        self._queue: Queue = Queue()
        self._active: bool = False
        self._thread: Thread = Thread(target=self._run, name="EventEngine")
        self._timer: Thread = Thread(target=self._run_timer, name="EventTimer")
        # 保护处理器列表的变更：register/unregister 可能在分发线程
        # 遍历时被任意线程调用。
        self._handler_lock: Lock = Lock()
        self._handlers: defaultdict[str, list[HandlerType]] = defaultdict(list)
        self._general_handlers: list[HandlerType] = []
        # 异步处理器：注入 asyncio 循环后在循环上调度
        self._async_handlers: defaultdict[str, list[AsyncHandlerType]] = defaultdict(list)
        self._async_general_handlers: list[AsyncHandlerType] = []
        # 注入的 asyncio 事件循环；为 None 时仅分发同步处理器
        self._loop: asyncio.AbstractEventLoop | None = None
        # 观测：各事件类型计数与最大分发延迟（供 /api/health 诊断积压）。
        # 读写仅发生在分发线程与定时器线程，字典操作本身线程安全。
        self._counts: defaultdict[str, int] = defaultdict(int)
        self._max_lag_ms: float = 0.0

    def _run(self) -> None:
        """分发线程主循环。"""
        while self._active:
            try:
                event: Event = self._queue.get(block=True, timeout=1)
                self._process(event)
            except Empty:
                pass

    def _process(self, event: Event) -> None:
        """先将事件分发给类型处理器，再分发给通用处理器。

        同步处理器在分发线程上直接调用；异步处理器（若注入了
        事件循环）经 ``call_soon_threadsafe`` 投递到循环上调度，
        从而让需要进入 asyncio 世界的消费者不必再各自外挂桥接。
        """
        self._counts[event.type] += 1
        if event.enqueued_at > 0:
            lag_ms = (monotonic() - event.enqueued_at) * 1000
            if lag_ms > self._max_lag_ms:
                self._max_lag_ms = lag_ms

        with self._handler_lock:
            handlers = list(self._handlers.get(event.type, ()))
            general = list(self._general_handlers)
            async_handlers = list(self._async_handlers.get(event.type, ()))
            async_general = list(self._async_general_handlers)

        for handler in handlers:
            self._safe_call(handler, event)

        for handler in general:
            self._safe_call(handler, event)

        # 异步处理器投递到事件循环（仅当注入了运行中的 loop）
        if self._loop is not None and self._loop.is_running():
            for handler in async_handlers:
                self._schedule_async(handler, event)
            for handler in async_general:
                self._schedule_async(handler, event)

    @staticmethod
    def _safe_call(handler: HandlerType, event: Event) -> None:
        """调用处理器，并将其异常与分发线程隔离。"""
        try:
            handler(event)
        except Exception:
            sys.stderr.write(
                f"Exception in event handler {handler!r} for event "
                f"{event.type!r}:\n{traceback.format_exc()}"
            )

    def _schedule_async(self, handler: AsyncHandlerType, event: Event) -> None:
        """将异步处理器经事件循环调度（分发线程 → asyncio 循环）。

        若循环已关闭（关停竞态），静默丢弃——与同步 ``_safe_call``
        的"不杀分发线程"语义一致。
        """
        assert self._loop is not None
        try:
            self._loop.call_soon_threadsafe(self._create_async_task, handler, event)
        except RuntimeError:
            # 事件循环已关闭：静默丢弃
            pass

    def _create_async_task(self, handler: AsyncHandlerType, event: Event) -> None:
        """在事件循环上创建并调度任务（由 ``call_soon_threadsafe`` 调用）。"""
        if self._loop is None:
            return
        try:
            task = self._loop.create_task(handler(event))
            task.add_done_callback(self._on_async_done)
        except Exception:
            sys.stderr.write(
                f"Failed to schedule async handler {handler!r} for event "
                f"{event.type!r}:\n{traceback.format_exc()}"
            )

    @staticmethod
    def _on_async_done(task: asyncio.Task) -> None:
        """异步任务完成回调：隔离并记录异常，避免被 asyncio 默认吞掉。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            sys.stderr.write(f"Exception in async event handler:\n{tb}")

    def _run_timer(self) -> None:
        """按固定间隔生成定时器事件。"""
        while self._active:
            sleep(self._interval)
            self.put(Event(EVENT_TIMER))

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """启动分发线程和定时器线程。

        可选注入一个 asyncio 事件循环；注入后，通过
        :meth:`register_async` 注册的异步处理器会在该循环上调度。
        """
        if self._active:
            # 已启动：仅更新循环（若提供）
            if loop is not None:
                self._loop = loop
            return
        self._loop = loop
        self._active = True
        self._thread.start()
        self._timer.start()

    def stop(self) -> None:
        """停止引擎。幂等操作。

        在关闭分发线程之前，会先排空队列中已有的事件，
        以免迟到的委托/成交事件丢失（它们可能仍需由
        DataEngine 持久化）。
        """
        if not self._active:
            return
        self._active = False
        self._timer.join()
        self._thread.join()
        # 同步排空剩余事件（分发线程已停止）
        while True:
            try:
                event = self._queue.get(block=False)
            except Empty:
                break
            self._process(event)

    @property
    def queue_size(self) -> int:
        """待处理事件的近似数量（用于健康状态报告）。"""
        return self._queue.qsize()

    @property
    def stats(self) -> dict:
        """事件流观测指标（health 端点诊断积压用）。

        - ``top_events``：按计数排序的事件类型（截取前 8）；
        - ``max_lag_ms``：事件从入队到被分发的最大延迟（毫秒，
        进程生命周期内的最大值）。
        """
        top = sorted(self._counts.items(), key=lambda kv: -kv[1])[:8]
        return {
            "top_events": [{"type": t, "count": n} for t, n in top],
            "max_lag_ms": round(self._max_lag_ms, 1),
        }

    def put(self, event: Event) -> None:
        """将事件放入队列。可从任意线程安全调用。"""
        event.enqueued_at = monotonic()
        self._queue.put(event)

    def register(self, type: str, handler: HandlerType) -> None:
        """为特定事件类型注册处理器（每种类型仅注册一次）。"""
        with self._handler_lock:
            handler_list: list[HandlerType] = self._handlers[type]
            if handler not in handler_list:
                handler_list.append(handler)

    def unregister(self, type: str, handler: HandlerType) -> None:
        """从特定事件类型注销处理器。"""
        with self._handler_lock:
            handler_list: list[HandlerType] = self._handlers[type]

            if handler in handler_list:
                handler_list.remove(handler)

            if not handler_list:
                self._handlers.pop(type, None)

    def register_general(self, handler: HandlerType) -> None:
        """为所有事件类型注册处理器。"""
        with self._handler_lock:
            if handler not in self._general_handlers:
                self._general_handlers.append(handler)

    def unregister_general(self, handler: HandlerType) -> None:
        """注销通用处理器。"""
        with self._handler_lock:
            if handler in self._general_handlers:
                self._general_handlers.remove(handler)

    # ------------------------------------------------------------------
    # 异步处理器：在注入的 asyncio 事件循环上调度
    # ------------------------------------------------------------------
    def register_async(self, type: str, handler: AsyncHandlerType) -> None:
        """为特定事件类型注册异步处理器（在事件循环上调度）。

        需先在 :meth:`start` 中注入运行中的事件循环，否则处理器
        不会被触发（同步处理器不受影响）。
        """
        with self._handler_lock:
            handler_list: list[AsyncHandlerType] = self._async_handlers[type]
            if handler not in handler_list:
                handler_list.append(handler)

    def unregister_async(self, type: str, handler: AsyncHandlerType) -> None:
        """从特定事件类型注销异步处理器。"""
        with self._handler_lock:
            handler_list: list[AsyncHandlerType] = self._async_handlers[type]
            if handler in handler_list:
                handler_list.remove(handler)
            if not handler_list:
                self._async_handlers.pop(type, None)

    def register_async_general(self, handler: AsyncHandlerType) -> None:
        """为所有事件类型注册异步通用处理器。"""
        with self._handler_lock:
            if handler not in self._async_general_handlers:
                self._async_general_handlers.append(handler)

    def unregister_async_general(self, handler: AsyncHandlerType) -> None:
        """注销异步通用处理器。"""
        with self._handler_lock:
            if handler in self._async_general_handlers:
                self._async_general_handlers.remove(handler)
