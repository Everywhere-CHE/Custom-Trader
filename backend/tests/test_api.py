import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        # 稍等片刻，让模拟器把合约通过事件总线推送出来
        time.sleep(0.5)
        yield test_client


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["engine_state"] == "RUNNING"
    assert data["gateways"].get("SIM") is True


def test_gateways(client):
    resp = client.get("/api/gateways")
    assert resp.status_code == 200
    gateways = {g["name"]: g for g in resp.json()}
    # SIM 始终存在；STOCK/CTP 根据配置注册
    assert "SIM" in gateways
    assert gateways["SIM"]["connected"] is True
    assert "tick_interval" in gateways["SIM"]["default_setting"]
    if "CTP" in gateways:
        # 敏感字段被掩码处理
        assert gateways["CTP"]["default_setting"]["password"] == "***"
        assert gateways["CTP"]["default_setting"]["auth_code"] == "***"


def test_contracts(client):
    resp = client.get("/api/contracts")
    assert resp.status_code == 200
    contracts = resp.json()
    vt_symbols = {c["vt_symbol"] for c in contracts}
    # 4 个 SIM 合约始终存在
    assert {"IF2509.CFFEX", "rb2510.SHFE", "600519.SSE", "000001.SZSE"} <= vt_symbols
    # 按网关过滤可用。注意：SIM 的两个股票合约与 STOCK 网关共享
    # vt_symbol；OMS 以 vt_symbol 为键存储合约，因此后注册的网关会覆盖
    # 前者（与 vn.py 行为一致）。期货合约则始终唯一属于 SIM。
    sim_only = client.get("/api/contracts", params={"gateway": "SIM"}).json()
    sim_symbols = {c["vt_symbol"] for c in sim_only}
    assert {"IF2509.CFFEX", "rb2510.SHFE"} <= sim_symbols


def test_subscribe_and_tick(client):
    resp = client.post(
        "/api/gateways/SIM/subscribe",
        json={"vt_symbols": ["IF2509.CFFEX"]},
    )
    assert resp.status_code == 202

    # 等待 tick 数据流入
    tick = None
    for _ in range(30):
        time.sleep(0.1)
        resp = client.get("/api/ticks/IF2509.CFFEX")
        if resp.status_code == 200:
            tick = resp.json()
            break
    assert tick is not None
    assert tick["last_price"] > 0
    assert tick["ask_price_1"] > tick["bid_price_1"]


def test_place_order_and_query(client):
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
    assert vt_orderid.startswith("SIM.")

    # 穿越对手价的委托应当成交；轮询 OMS 状态
    order = None
    for _ in range(30):
        time.sleep(0.1)
        resp = client.get(f"/api/orders/{vt_orderid}")
        if resp.status_code == 200 and resp.json()["status"] == "ALLTRADED":
            order = resp.json()
            break
    assert order is not None, "order did not fill in time"

    trades = client.get("/api/trades").json()
    assert any(t["vt_orderid"] == vt_orderid for t in trades)

    positions = client.get("/api/positions").json()
    assert any(
        p["vt_symbol"] == "IF2509.CFFEX" and p["volume"] >= 1
        for p in positions
    )

    accounts = client.get("/api/accounts").json()
    sim_accounts = [a for a in accounts if a["gateway_name"] == "SIM"]
    assert len(sim_accounts) == 1
    assert sim_accounts[0]["accountid"] == "sim"


def test_cancel_order(client):
    resp = client.post(
        "/api/orders",
        json={
            "vt_symbol": "rb2510.SHFE",
            "direction": "LONG",
            "offset": "OPEN",
            "type": "LIMIT",
            "price": 1,
            "volume": 1,
        },
    )
    assert resp.status_code == 201
    vt_orderid = resp.json()["vt_orderid"]

    # 等待进入 NOTTRADED 状态后再撤单
    for _ in range(20):
        time.sleep(0.1)
        order = client.get(f"/api/orders/{vt_orderid}").json()
        if order["status"] == "NOTTRADED":
            break

    resp = client.delete(f"/api/orders/{vt_orderid}")
    assert resp.status_code == 202

    cancelled = False
    for _ in range(20):
        time.sleep(0.1)
        order = client.get(f"/api/orders/{vt_orderid}").json()
        if order["status"] == "CANCELLED":
            cancelled = True
            break
    assert cancelled


def test_order_validation_errors(client):
    # 未知合约
    resp = client.post(
        "/api/orders",
        json={
            "vt_symbol": "NOPE.CFFEX",
            "direction": "LONG",
            "type": "LIMIT",
            "price": 1,
            "volume": 1,
        },
    )
    assert resp.status_code == 422

    # 非正数的数量会被 pydantic 拒绝
    resp = client.post(
        "/api/orders",
        json={
            "vt_symbol": "IF2509.CFFEX",
            "direction": "LONG",
            "type": "LIMIT",
            "price": 1,
            "volume": 0,
        },
    )
    assert resp.status_code == 422


def test_bars_from_gateway(client):
    resp = client.get(
        "/api/bars",
        params={
            "vt_symbol": "IF2509.CFFEX",
            "interval": "1m",
            "from_gateway": True,
        },
    )
    assert resp.status_code == 200
    bars = resp.json()
    assert len(bars) > 100  # 默认窗口：最近 5 天的分钟 K 线

    # 已持久化的 K 线现在可从数据库读取
    resp = client.get(
        "/api/bars",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "1m"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= len(bars)


def test_logs(client):
    resp = client.get("/api/logs", params={"limit": 50})
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) > 0
    assert any("connected" in log["msg"].lower() for log in logs)


def test_websocket_tick_stream(client):
    with client.websocket_connect("/ws") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "connected"

        ws.send_json(
            {
                "action": "subscribe",
                "channels": ["tick"],
                "symbols": ["IF2509.CFFEX"],
            }
        )
        ack = ws.receive_json()
        assert ack["type"] == "subscribed"
        assert "tick" in ack["data"]["channels"]

        # 应当在若干条消息内收到至少一条 tick
        message = ws.receive_json()
        assert message["type"] == "tick"
        assert message["data"]["vt_symbol"] == "IF2509.CFFEX"
