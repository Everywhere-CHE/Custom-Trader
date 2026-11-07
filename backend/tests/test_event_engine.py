"""基于线程的 EventEngine 测试。"""

import threading
import time

from app.core.event import EVENT_TIMER, Event, EventEngine


def test_register_and_dispatch():
    engine = EventEngine(interval=10)  # 长定时器周期，保持安静不触发
    received: list[Event] = []

    engine.register("eTest", received.append)
    engine.start()
    try:
        engine.put(Event("eTest", {"value": 1}))
        time.sleep(0.5)
    finally:
        engine.stop()

    assert len(received) == 1
    assert received[0].data == {"value": 1}


def test_put_from_other_thread():
    """从任意线程 put 的事件必须被正确分发（CTP 风格）。"""
    engine = EventEngine(interval=10)
    received: list[Event] = []
    engine.register("eThread", received.append)
    engine.start()
    try:
        def producer():
            for i in range(10):
                engine.put(Event("eThread", i))

        threads = [threading.Thread(target=producer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.5)
    finally:
        engine.stop()

    assert len(received) == 40


def test_handler_exception_does_not_kill_dispatcher():
    engine = EventEngine(interval=10)
    received: list[Event] = []

    def bad_handler(event: Event) -> None:
        raise RuntimeError("boom")

    engine.register("eBad", bad_handler)
    engine.register("eBad", received.append)
    engine.start()
    try:
        engine.put(Event("eBad", 1))
        engine.put(Event("eBad", 2))
        time.sleep(0.5)
    finally:
        engine.stop()

    # 坏处理器之后的好处理器仍然运行了两次
    assert len(received) == 2


def test_stop_is_idempotent():
    engine = EventEngine(interval=10)
    engine.start()
    engine.stop()
    engine.stop()  # 不得抛出异常


def test_timer_event():
    engine = EventEngine(interval=0.1)
    count = {"n": 0}

    def on_timer(event: Event) -> None:
        count["n"] += 1

    engine.register(EVENT_TIMER, on_timer)
    engine.start()
    try:
        time.sleep(0.5)
    finally:
        engine.stop()

    assert count["n"] >= 2


def test_unregister():
    engine = EventEngine(interval=10)
    received: list[Event] = []
    engine.register("eX", received.append)
    engine.unregister("eX", received.append)
    engine.start()
    try:
        engine.put(Event("eX", 1))
        time.sleep(0.3)
    finally:
        engine.stop()
    assert not received


def test_async_handler_dispatch():
    """异步处理器在注入的事件循环上被调度。"""
    import asyncio

    engine = EventEngine(interval=10)  # 长定时器周期，保持安静
    received: list[Event] = []

    async def on_async(event: Event) -> None:
        received.append(event)

    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    engine.register_async("eAsync", on_async)
    engine.start(loop=loop)
    runner.start()
    try:
        engine.put(Event("eAsync", {"v": 1}))
        time.sleep(0.5)
    finally:
        engine.stop()
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=2)
        loop.close()

    assert len(received) == 1
    assert received[0].data == {"v": 1}


def test_async_silent_without_loop():
    """未注入 loop 时异步处理器静默不触发，同步处理器照常工作。"""
    engine = EventEngine(interval=10)
    sync_received: list[Event] = []
    async_received: list[Event] = []

    async def on_async(event: Event) -> None:
        async_received.append(event)

    engine.register("eBoth", sync_received.append)
    engine.register_async("eBoth", on_async)
    engine.start()  # 无 loop
    try:
        engine.put(Event("eBoth", 1))
        time.sleep(0.5)
    finally:
        engine.stop()

    assert len(sync_received) == 1
    assert async_received == []
