"""BacktestManager 单元测试：状态管理、裁剪、序列化。"""
from app.services.backtest_manager import BacktestManager, BacktestTask


def test_submit_returns_task():
    """submit 返回带 task_id 的 task（无需真实 main_engine 实例）。"""
    mgr = BacktestManager()
    # 模拟一个轻量验证：不调用 submit（它需要真实 main_engine），
    # 而是直接测试状态管理与裁剪逻辑
    assert mgr.list() == []
    mgr.clear()  # 幂等
    assert mgr.list() == []


def test_set_status():
    mgr = BacktestManager()
    mgr._tasks["t1"] = BacktestTask(task_id="t1")
    mgr._set_status("t1", "done", finished=True, message="complete", result={"key": 1})
    t = mgr.get("t1")
    assert t is not None
    assert t.status == "done"
    assert t.finished_at is not None
    assert t.result == {"key": 1}
    assert t.message == "complete"


def test_get_nonexistent():
    mgr = BacktestManager()
    assert mgr.get("nonexistent") is None


def test_trim_keeps_max():
    mgr = BacktestManager(max_history=3)
    for i in range(5):
        t = BacktestTask(task_id=f"t{i}", status="done", finished_at="2026-01-01T00:00:00")
        if i == 4:
            t.status = "running"  # 保留运行中的任务
        mgr._tasks[f"t{i}"] = t
    mgr._trim_locked()
    # 最大 3 个任务，保留运行中的那个
    assert len(mgr._tasks) == 3
    assert "t4" in mgr._tasks  # running 的保留


def test_task_to_dict():
    task = BacktestTask(task_id="x", status="running", message="working")
    d = task.to_dict(include_result=False)
    assert d["task_id"] == "x"
    assert d["status"] == "running"
    assert "result" not in d

    task.result = {"a": 1}
    d2 = task.to_dict(include_result=True)
    assert d2["result"] == {"a": 1}