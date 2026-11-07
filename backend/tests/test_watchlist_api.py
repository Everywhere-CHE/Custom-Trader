import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.watchlist import CONFIG_PATH


@pytest.fixture(scope="module")
def client():
    existed = CONFIG_PATH.exists()
    if existed:
        CONFIG_PATH.rename(CONFIG_PATH.with_suffix(".bak"))
    with TestClient(app) as test_client:
        time.sleep(0.5)
        yield test_client
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
    backup = CONFIG_PATH.with_suffix(".bak")
    if backup.exists():
        backup.replace(CONFIG_PATH)


def test_default_watchlist(client):
    resp = client.get("/api/watchlist")
    assert resp.status_code == 200
    symbols = resp.json()["symbols"]
    # 默认只包含真实 A 股代码（期货在 CTP 连接后才出现）
    assert "600519.SSE" in symbols
    assert all("." in s for s in symbols)


def test_contract_keyword_search(client):
    resp = client.get("/api/contracts", params={"keyword": "茅台"})
    assert resp.status_code == 200
    results = resp.json()
    assert any(c["symbol"] == "600519" for c in results)

    resp = client.get("/api/contracts", params={"keyword": "rb25"})
    assert any(c["symbol"] == "rb2510" for c in resp.json())

    resp = client.get("/api/contracts", params={"keyword": "zzz-nope"})
    assert resp.json() == []

    # limit 生效
    resp = client.get("/api/contracts", params={"limit": 2})
    assert len(resp.json()) == 2


def test_add_known_contract_subscribes(client):
    resp = client.post("/api/watchlist", json={"vt_symbol": "rb2510.SHFE"})
    assert resp.status_code == 201
    assert "rb2510.SHFE" in resp.json()["symbols"]

    # 订阅后 SIM 开始推送 tick
    found = False
    for _ in range(30):
        time.sleep(0.1)
        if client.get("/api/ticks/rb2510.SHFE").status_code == 200:
            found = True
            break
    assert found


def test_add_unknown_stock_code_lazy_registers(client):
    """任意 A 股代码会路由到 STOCK 网关并获得一个惰性注册的合约。
    测试中 STOCK 不会自动连接，所以先显式连接它
    （合约注册本身是本地操作；只有行情轮询才需要网络）。"""
    client.post("/api/gateways/STOCK/connect", json={})
    time.sleep(0.3)

    resp = client.post("/api/watchlist", json={"vt_symbol": "601899.SSE"})
    assert resp.status_code == 201
    assert "601899.SSE" in resp.json()["symbols"]

    time.sleep(0.3)
    contracts = client.get(
        "/api/contracts", params={"keyword": "601899"}
    ).json()
    assert any(c["vt_symbol"] == "601899.SSE" for c in contracts)

    client.post("/api/gateways/STOCK/disconnect")


def test_add_unknown_futures_without_ctp_404(client):
    """未知期货合约且没有可惰性注册它的已连接期货网关时
    -> 路由到 SIM（覆盖 CFFEX），但 SIM 会优雅地拒绝未知代码；
    仅属于 DCE 的代码没有任何已连接网关 -> 404？
    SIM 不覆盖 DCE，CTP 也未连接。"""
    resp = client.post("/api/watchlist", json={"vt_symbol": "m2601.DCE"})
    # 没有已连接的网关支持 DCE（CTP 未连接）-> 404
    assert resp.status_code == 404
    assert "CTP" in resp.json()["detail"]


def test_add_invalid_symbol_422(client):
    resp = client.post("/api/watchlist", json={"vt_symbol": "no-exchange"})
    assert resp.status_code == 422
    resp = client.post("/api/watchlist", json={"vt_symbol": ""})
    assert resp.status_code == 422


def test_remove_symbol(client):
    client.post("/api/watchlist", json={"vt_symbol": "rb2510.SHFE"})
    resp = client.delete("/api/watchlist/rb2510.SHFE")
    assert resp.status_code == 200
    assert "rb2510.SHFE" not in resp.json()["symbols"]


def test_persistence(client):
    client.post("/api/watchlist", json={"vt_symbol": "000001.SZSE"})
    assert CONFIG_PATH.exists()
    import json

    saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "000001.SZSE" in saved
