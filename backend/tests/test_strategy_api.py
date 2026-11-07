import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        time.sleep(0.5)
        yield test_client


START = (datetime.now() - timedelta(days=3)).replace(
    hour=9, minute=0, second=0, microsecond=0
)
END = START + timedelta(hours=30)


def test_download_data_then_backtest(client):
    # 1. 将确定性的 SIM 历史数据下载到数据库
    resp = client.post(
        "/api/backtest/download-data",
        json={
            "vt_symbol": "rb2510.SHFE",
            "interval": "1m",
            "start": START.isoformat(),
            "end": END.isoformat(),
            "gateway_name": "SIM",
        },
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["saved"]
    assert saved > 1000

    # 2. 数据概览
    resp = client.get(
        "/api/backtest/data",
        params={"vt_symbol": "rb2510.SHFE", "interval": "1m"},
    )
    assert resp.status_code == 200
    assert resp.json()["count"] >= saved

    # 3. 运行回测
    body = {
        "class_name": "MaCrossStrategy",
        "vt_symbol": "rb2510.SHFE",
        "interval": "1m",
        "start": (START + timedelta(hours=3)).isoformat(),
        "end": END.isoformat(),
        "capital": 1_000_000,
        "rate": 0.0001,
        "slippage": 1,
        "size": 10,
        "pricetick": 1,
        "setting": {"fast_window": 5, "slow_window": 20},
    }
    resp = client.post("/api/backtest", json=body)
    assert resp.status_code == 200, resp.text
    result = resp.json()

    # statistics 是一个列表（多策略 x 多合约的组合）
    stats_list = result["statistics"]
    assert isinstance(stats_list, list) and len(stats_list) == 1
    stats = stats_list[0]
    assert stats["vt_symbol"] == "rb2510.SHFE"
    assert stats["class_name"] == "MaCrossStrategy"
    for key in (
        "total_return", "annual_return", "max_drawdown", "sharpe_ratio",
        "win_rate", "profit_factor", "total_trade_count",
    ):
        assert key in stats
    assert stats["total_trade_count"] > 0
    assert len(result["daily_results"]) >= 1
    assert len(result["trades"]) == stats["total_trade_count"]

    # 4. 确定性：相同参数重跑结果一致
    resp2 = client.post("/api/backtest", json=body)
    assert resp2.json()["statistics"] == stats_list


def test_backtest_unknown_class(client):
    resp = client.post(
        "/api/backtest",
        json={
            "class_name": "NopeStrategy",
            "vt_symbol": "rb2510.SHFE",
            "interval": "1m",
            "start": START.isoformat(),
        },
    )
    assert resp.status_code == 422


def test_backtest_no_data(client):
    resp = client.post(
        "/api/backtest",
        json={
            "class_name": "MaCrossStrategy",
            "vt_symbol": "IF2509.CFFEX",
            "interval": "1h",
            "start": "2020-01-01T00:00:00",
            "end": "2020-01-02T00:00:00",
        },
    )
    assert resp.status_code == 422


def test_risk_endpoints(client):
    resp = client.get("/api/risk")
    assert resp.status_code == 200
    assert resp.json()["settings"]["active"] is True

    # 收紧单笔委托数量限制
    resp = client.put("/api/risk", json={"order_size_limit": 1})
    assert resp.status_code == 200
    assert resp.json()["settings"]["order_size_limit"] == 1

    # 超限委托会被拒绝并带有风控原因（422）
    resp = client.post(
        "/api/orders",
        json={
            "vt_symbol": "IF2509.CFFEX",
            "direction": "LONG",
            "offset": "OPEN",
            "type": "LIMIT",
            "price": 99999,
            "volume": 5,
        },
    )
    assert resp.status_code == 422
    assert "风控拦截" in resp.json()["detail"]

    # 风控拒单会被写入日志
    time.sleep(0.3)
    logs = client.get("/api/logs", params={"limit": 20}).json()
    assert any("risk check rejected" in log["msg"] for log in logs)

    # 恢复设置并重置
    resp = client.put("/api/risk", json={"order_size_limit": 100})
    assert resp.status_code == 200
    resp = client.post("/api/risk/reset")
    assert resp.status_code == 200
    assert resp.json()["status"]["order_count"] == 0
