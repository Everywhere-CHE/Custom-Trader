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


def test_intraday_bars_from_real_ticks(client):
    # 订阅后 SIM 生成真实 tick，由 DataEngine 持久化
    client.post(
        "/api/gateways/SIM/subscribe",
        json={"vt_symbols": ["IF2509.CFFEX"]},
    )
    # 等待 tick 流入以及 1 秒批量落盘任务运行
    time.sleep(3)

    resp = client.get(
        "/api/bars/intraday", params={"vt_symbol": "IF2509.CFFEX"}
    )
    assert resp.status_code == 200
    bars = resp.json()
    assert len(bars) >= 1

    bar = bars[-1]
    # 真实的 OHLC 不变量
    assert bar["high_price"] >= bar["low_price"]
    assert bar["low_price"] <= bar["open_price"] <= bar["high_price"]
    assert bar["low_price"] <= bar["close_price"] <= bar["high_price"]
    # 时间戳对齐到分钟
    assert bar["datetime"].endswith(":00")

    # 时间升序且分钟唯一
    datetimes = [b["datetime"] for b in bars]
    assert datetimes == sorted(datetimes)
    assert len(datetimes) == len(set(datetimes))


def test_intraday_bars_match_last_tick(client):
    """最后一根分时 K 线必须反映当前真实 tick 价格
    （证明 K 线来自真实数据而非合成）。"""
    time.sleep(1.5)
    tick = client.get("/api/ticks/IF2509.CFFEX").json()
    bars = client.get(
        "/api/bars/intraday", params={"vt_symbol": "IF2509.CFFEX"}
    ).json()
    last_bar = bars[-1]
    # 当前价格落在最后一根（或尚在进行中的前一根）K 线的
    # 价格区间内——放宽窗口：对最近几根 K 线做检查
    recent_low = min(b["low_price"] for b in bars[-3:])
    recent_high = max(b["high_price"] for b in bars[-3:])
    assert recent_low <= tick["last_price"] <= recent_high or (
        # tick 可能刚开启一个尚未落盘的新分钟
        abs(tick["last_price"] - last_bar["close_price"])
        < last_bar["close_price"] * 0.01
    )


def test_intraday_bars_empty_for_unsubscribed(client):
    resp = client.get(
        "/api/bars/intraday", params={"vt_symbol": "600036.SSE"}
    )
    assert resp.status_code == 200
    # 本次测试会话中该合约没有或几乎没有 tick 记录
    assert isinstance(resp.json(), list)


def test_intraday_invalid_symbol(client):
    resp = client.get("/api/bars/intraday", params={"vt_symbol": "bad"})
    assert resp.status_code == 422


def test_intraday_hourly_daily_weekly_aggregation(client):
    """更高时间框架现在包含数月的已下载历史，外加
    由实时 tick 聚合出的尾部。"""
    time.sleep(1.5)
    minute = client.get(
        "/api/bars/intraday",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "1m"},
    ).json()
    hourly = client.get(
        "/api/bars/intraday",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "1h"},
    ).json()
    daily = client.get(
        "/api/bars/intraday",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "d"},
    ).json()
    weekly = client.get(
        "/api/bars/intraday",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "w"},
    ).json()

    # 1m 保持纯实时；更高时间框架带有数月历史
    assert len(minute) >= 1
    assert len(hourly) >= 100     # 约 3 个月的小时线历史
    assert len(daily) >= 100      # 约 12 个月的日线历史
    assert len(weekly) >= 50      # 约 24 个月的周线历史

    # 桶对齐
    assert hourly[-1]["datetime"].endswith(":00:00")
    assert daily[-1]["datetime"].endswith("T00:00:00")
    assert weekly[-1]["datetime"].endswith("T00:00:00")
    # 实时尾部的周线桶从周一开始
    from datetime import datetime as dt

    assert dt.fromisoformat(weekly[-1]["datetime"]).weekday() == 0

    # 合并后依然时间升序且唯一
    for series in (hourly, daily, weekly):
        datetimes = [b["datetime"] for b in series]
        assert datetimes == sorted(datetimes)
        assert len(datetimes) == len(set(datetimes))

    # 实时尾部确实存在：最后一根日线就是今天的真实 K 线
    assert daily[-1]["datetime"][:10] == minute[-1]["datetime"][:10]

    # interval 标签能够往返一致
    assert hourly[-1]["interval"] == "1h"
    assert weekly[-1]["interval"] == "w"


def test_intraday_bad_interval(client):
    # 现在 5m/15m/30m 是受支持的聚合周期；真正未知的
    # interval 字符串必须返回 422。
    resp = client.get(
        "/api/bars/intraday",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "5m"},
    )
    assert resp.status_code == 200

    resp = client.get(
        "/api/bars/intraday",
        params={"vt_symbol": "IF2509.CFFEX", "interval": "7m"},
    )
    assert resp.status_code == 422
