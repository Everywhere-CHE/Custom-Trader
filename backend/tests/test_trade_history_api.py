import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        time.sleep(0.5)
        yield test_client


def _place_filled_order(client) -> str:
    resp = client.post(
        "/api/orders",
        json={
            "vt_symbol": "IF2509.CFFEX",
            "direction": "LONG",
            "offset": "OPEN",
            "type": "LIMIT",
            "price": 99999,
            "volume": 1,
        },
    )
    assert resp.status_code == 201
    vt_orderid = resp.json()["vt_orderid"]
    for _ in range(30):
        time.sleep(0.1)
        order = client.get(f"/api/orders/{vt_orderid}").json()
        if order["status"] == "ALLTRADED":
            return vt_orderid
    raise AssertionError("order did not fill")


def test_history_survives_memory_loss(client):
    """模拟一次重启：清空 OMS 内存缓存，验证查询端点
    仍能从数据库返回记录。"""
    vt_orderid = _place_filled_order(client)
    # 给 DataEngine 的事件处理器一点时间完成提交
    time.sleep(1.5)

    main_engine = client.app.state.main_engine
    oms = main_engine.oms

    trade_ids_before = {
        t["vt_tradeid"] for t in client.get("/api/trades").json()
    }
    assert trade_ids_before, "no trades before memory clear"

    # “重启”：清空内存状态
    saved_orders = dict(oms.orders)
    saved_trades = dict(oms.trades)
    oms.orders.clear()
    oms.trades.clear()
    try:
        trades = client.get("/api/trades").json()
        orders = client.get("/api/orders").json()

        # 历史记录仍可从数据库查询到
        assert any(t["vt_orderid"] == vt_orderid for t in trades)
        assert any(o["vt_orderid"] == vt_orderid for o in orders)
        assert {t["vt_tradeid"] for t in trades} >= trade_ids_before

        # history=false -> 仅内存 -> 此时应为空
        assert client.get("/api/trades", params={"history": False}).json() == []
    finally:
        oms.orders.update(saved_orders)
        oms.trades.update(saved_trades)


def test_memory_wins_on_duplicate(client):
    """当同一条记录同时存在于内存和数据库时，返回内存中（更新鲜）
    的版本，且只返回一次。"""
    vt_orderid = _place_filled_order(client)
    time.sleep(1.5)

    orders = client.get("/api/orders").json()
    matches = [o for o in orders if o["vt_orderid"] == vt_orderid]
    assert len(matches) == 1
    assert matches[0]["status"] == "ALLTRADED"


def test_trades_sorted_newest_first_and_limited(client):
    _place_filled_order(client)
    _place_filled_order(client)
    time.sleep(1.5)

    trades = client.get("/api/trades").json()
    datetimes = [t["datetime"] or "" for t in trades]
    assert datetimes == sorted(datetimes, reverse=True)

    limited = client.get("/api/trades", params={"limit": 1}).json()
    assert len(limited) == 1
    assert limited[0]["vt_tradeid"] == trades[0]["vt_tradeid"]


def test_date_range_filter(client):
    _place_filled_order(client)
    time.sleep(1.5)
    # 极远未来的 start 会把数据库记录全部排除；内存部分也会在合并时
    # 被过滤（内存条目没有日期过滤，因此改用关闭 history 的对比方式）：
    # 使用不可能的时间范围 + 清空的内存
    main_engine = client.app.state.main_engine
    oms = main_engine.oms
    saved = dict(oms.trades)
    oms.trades.clear()
    try:
        trades = client.get(
            "/api/trades", params={"start": "2099-01-01T00:00:00"}
        ).json()
        assert trades == []
    finally:
        oms.trades.update(saved)
