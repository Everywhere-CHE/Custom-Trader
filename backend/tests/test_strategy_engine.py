import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        time.sleep(0.5)  # 让 SIM 推送合约
        yield test_client


def wait_for(client, name, predicate, timeout=60.0):
    """轮询 GET /api/strategies/{name} 直到 predicate(data) 为真。

    超时设置得比较宽松：数据库为空时 on_init 会先下载并保存
    约 43k 根 1 分钟 K 线（30 天），策略才会切换到 inited 状态。
    """
    deadline = time.time() + timeout
    data = None
    while time.time() < deadline:
        resp = client.get(f"/api/strategies/{name}")
        if resp.status_code == 200:
            data = resp.json()
            if predicate(data):
                return data
        time.sleep(0.2)
    return data


def test_strategy_classes_available(client):
    resp = client.get("/api/strategies/classes")
    assert resp.status_code == 200
    classes = {c["class_name"]: c for c in resp.json()}
    assert "MaCrossStrategy" in classes
    assert "GridStrategy" in classes
    assert "DonchianStrategy" in classes
    assert classes["MaCrossStrategy"]["parameters"]["fast_window"] == 10


def test_create_init_start_stop_lifecycle(client):
    # 创建
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "MaCrossStrategy",
            "name": "test_ma",
            "vt_symbol": "IF2509.CFFEX",
            "setting": {"fast_window": 3, "slow_window": 5, "fixed_size": 1},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["parameters"]["fast_window"] == 3
    assert data["variables"]["inited"] is False

    # 重名被拒绝
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "MaCrossStrategy",
            "name": "test_ma",
            "vt_symbol": "IF2509.CFFEX",
        },
    )
    assert resp.status_code == 409

    # 未知策略类被拒绝
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "NopeStrategy",
            "name": "test_x",
            "vt_symbol": "IF2509.CFFEX",
        },
    )
    assert resp.status_code == 422

    # 初始化之前启动会被拒绝
    resp = client.post("/api/strategies/test_ma/start")
    assert resp.status_code == 409

    # 初始化（异步）-> 等待直到 inited
    resp = client.post("/api/strategies/test_ma/init")
    assert resp.status_code == 202
    data = wait_for(client, "test_ma", lambda d: d["variables"]["inited"])
    assert data is not None and data["variables"]["inited"] is True

    # 启动
    resp = client.post("/api/strategies/test_ma/start")
    assert resp.status_code == 200
    assert resp.json()["variables"]["trading"] is True

    # 让 SIM 的 tick 流动一会（订阅已在初始化时完成）；极小的
    # 均线窗口可能交叉也可能不交叉，这里只要求主循环存活即可。
    time.sleep(2)

    # 运行中修改参数
    resp = client.put(
        "/api/strategies/test_ma", json={"setting": {"fixed_size": 2}}
    )
    assert resp.status_code == 200
    assert resp.json()["parameters"]["fixed_size"] == 2

    # 运行中删除会被拒绝
    resp = client.request("DELETE", "/api/strategies/test_ma")
    assert resp.status_code == 409

    # 停止
    resp = client.post("/api/strategies/test_ma/stop")
    assert resp.status_code == 200
    assert resp.json()["variables"]["trading"] is False

    # 停止后即可删除
    resp = client.request("DELETE", "/api/strategies/test_ma")
    assert resp.status_code == 200
    resp = client.get("/api/strategies/test_ma")
    assert resp.status_code == 404


def test_strategy_persisted_to_db(client):
    """创建的策略会写入 strategies 表。"""
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "GridStrategy",
            "name": "persist_grid",
            "vt_symbol": "rb2510.SHFE",
            "setting": {"grid_step": 5},
        },
    )
    assert resp.status_code == 201

    from app.db.models import StrategyModel
    from app.db.session import create_session

    session = create_session()
    try:
        row = (
            session.query(StrategyModel)
            .filter_by(name="persist_grid")
            .one_or_none()
        )
    finally:
        session.close()
    assert row is not None
    assert row.class_name == "GridStrategy"
    assert row.status == "STOPPED"

    # 通过 API 清理
    client.request("DELETE", "/api/strategies/persist_grid")


def test_pos_persisted_and_restored(client):
    """策略的 pos 在重启后仍然保留：每笔成交时持久化，
    由 load_strategies_from_db 恢复（回归：昨日持仓在
    重新打开后曾无法交易）。"""
    import json as jsonlib

    from app.db.models import StrategyModel
    from app.db.session import create_session

    # 创建一个策略，并假装一笔成交改变了它的 pos
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "MaCrossStrategy",
            "name": "pos_restore",
            "vt_symbol": "IF2509.CFFEX",
            "setting": {"fast_window": 3, "slow_window": 5},
        },
    )
    assert resp.status_code == 201

    from app.api.deps import get_main_engine  # noqa: F401  (路由依赖)

    # 直接访问运行中的引擎（TestClient 共享同一进程）
    engine = client.app.state.main_engine.get_engine("strategy")
    strategy = engine.strategies["pos_restore"]
    strategy.pos = 3.0
    engine._db_save_variables(strategy)

    # 数据库行中带有 pos
    session = create_session()
    try:
        row = (
            session.query(StrategyModel)
            .filter_by(name="pos_restore")
            .one_or_none()
        )
    finally:
        session.close()
    assert row is not None
    assert jsonlib.loads(row.variables)["pos"] == 3.0

    # 模拟一次重启：丢弃内存中的实例并重新加载
    engine.strategies.pop("pos_restore")
    engine.symbol_strategy_map["IF2509.CFFEX"] = [
        s
        for s in engine.symbol_strategy_map["IF2509.CFFEX"]
        if s.strategy_name != "pos_restore"
    ]
    engine.load_strategies_from_db()

    restored = engine.strategies["pos_restore"]
    assert restored.pos == 3.0

    # 并且 API 也能反映出来
    data = client.get("/api/strategies/pos_restore").json()
    assert data["variables"]["pos"] == 3.0

    client.request("DELETE", "/api/strategies/pos_restore")


def test_ws_strategy_channel(client):
    with client.websocket_connect("/ws") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "connected"
        ws.send_json(
            {"action": "subscribe", "channels": ["strategy", "strategy_log"]}
        )
        ack = ws.receive_json()
        assert "strategy" in ack["data"]["channels"]

        # 创建策略会推送一个 strategy 事件
        resp = client.post(
            "/api/strategies",
            json={
                "class_name": "MaCrossStrategy",
                "name": "ws_test",
                "vt_symbol": "IF2509.CFFEX",
            },
        )
        assert resp.status_code == 201

        message = ws.receive_json()
        assert message["type"] in ("strategy", "strategy_log")

    client.request("DELETE", "/api/strategies/ws_test")


def test_reconcile_and_sync_pos(client):
    """对账功能比较策略 pos 与网关净持仓；sync-pos 会覆盖
    已停止且不共用合约的策略的 pos（回归：停止期间的手动交易
    曾导致 pos 过期）。"""
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "MaCrossStrategy",
            "name": "recon_a",
            "vt_symbol": "IF2509.CFFEX",
            "setting": {"fast_window": 3, "slow_window": 5},
        },
    )
    assert resp.status_code == 201

    engine = client.app.state.main_engine.get_engine("strategy")
    strategy = engine.strategies["recon_a"]

    # 模拟过期的 pos：策略认为有 2 手，而网关没有持仓
    strategy.pos = 2.0

    rows = {r["strategy_name"]: r for r in client.get("/api/strategies/reconcile").json()}
    row = rows["recon_a"]
    assert row["strategy_pos"] == 2.0
    assert row["matched"] is False
    assert row["shared"] is False

    # 停止状态下同步 -> pos 变为网关净持仓
    resp = client.post("/api/strategies/recon_a/sync-pos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["old_pos"] == 2.0
    assert body["new_pos"] == row["gateway_net_pos"]
    assert engine.strategies["recon_a"].pos == row["gateway_net_pos"]

    # 现在已经对齐
    rows = {r["strategy_name"]: r for r in client.get("/api/strategies/reconcile").json()}
    assert rows["recon_a"]["matched"] is True

    # 共用合约的情况：第二个策略使用相同的 vt_symbol
    resp = client.post(
        "/api/strategies",
        json={
            "class_name": "MaCrossStrategy",
            "name": "recon_b",
            "vt_symbol": "IF2509.CFFEX",
            "setting": {"fast_window": 3, "slow_window": 5},
        },
    )
    assert resp.status_code == 201
    rows = {r["strategy_name"]: r for r in client.get("/api/strategies/reconcile").json()}
    assert rows["recon_a"]["shared"] is True
    # 共用合约时拒绝同步
    resp = client.post("/api/strategies/recon_a/sync-pos")
    assert resp.status_code == 409
    assert "共用" in resp.json()["detail"]

    # 未知策略 -> 404
    resp = client.post("/api/strategies/nope/sync-pos")
    assert resp.status_code == 404

    client.request("DELETE", "/api/strategies/recon_a")
    client.request("DELETE", "/api/strategies/recon_b")
