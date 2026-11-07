import pytest; pytest.skip("requires real CTP — SIM fixture removed", allow_module_level=True)  # noqa: E501

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.ai.tools import (
    TOOL_SPECS,
    ToolExecutionError,
    TradingTools,
    execute_tool,
)
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        time.sleep(0.5)
        yield test_client


@pytest.fixture(scope="module")
def tools(client):
    # TestClient 是 httpx.Client 的子类；base_url 会路由到应用内部
    return TradingTools(base_url="http://testserver/api", client=client)


@pytest.fixture(scope="module")
def trading_tools(client):
    return TradingTools(
        base_url="http://testserver/api", client=client, allow_trading=True
    )


def test_tool_specs_complete():
    assert len(TOOL_SPECS) == 28
    names = {s["name"] for s in TOOL_SPECS}
    assert "place_order" in names
    assert "run_backtest" in names
    # 第五阶段完善新增的工具
    assert {
        "get_trades", "get_performance", "get_intraday_bars",
        "get_gateways", "get_logs", "edit_strategy",
        "list_strategy_files", "read_strategy_file",
        "reconcile_positions", "sync_strategy_pos",
    } <= names
    # 每个 spec 都映射到一个真实方法
    for spec in TOOL_SPECS:
        assert hasattr(TradingTools, spec["name"]), spec["name"]
        assert "input_schema" in spec
        assert "description" in spec


def test_market_overview(tools):
    result = tools.get_market_overview()
    assert result["count"] >= 4
    row = result["contracts"][0]
    assert {"vt_symbol", "name", "last", "chg_pct", "volume"} <= set(row)


def test_get_tick_compact(client, tools):
    client.post(
        "/api/gateways/SIM/subscribe",
        json={"vt_symbols": ["IF2509.CFFEX"]},
    )
    time.sleep(0.5)
    tick = tools.get_tick("IF2509.CFFEX")
    # 精简格式：不含五档行情字段
    assert "bid1" in tick and "bid_price_3" not in tick
    assert tick["last"] > 0


def test_get_bars_compact_array(client, tools):
    tools.download_backtest_data(
        "rb2510.SHFE", "2026-07-01T09:00:00", "2026-07-01T12:00:00"
    )
    result = tools.get_bars("rb2510.SHFE", "1m", limit=50)
    assert result["count"] > 0
    assert result["columns"][0] == "datetime"
    assert len(result["bars"][0]) == 6


def test_place_order_gated_by_default(tools):
    with pytest.raises(ToolExecutionError, match="AI 交易未启用"):
        tools.place_order("IF2509.CFFEX", "LONG", 1, price=99999)


def test_sync_strategy_pos_gated_by_default(tools):
    with pytest.raises(ToolExecutionError, match="AI 交易未启用"):
        tools.sync_strategy_pos("whatever")


def test_get_gateways(tools):
    gateways = tools.get_gateways()
    names = {g["name"] for g in gateways}
    assert "SIM" in names
    sim = next(g for g in gateways if g["name"] == "SIM")
    assert sim["connected"] is True
    assert "CFFEX" in sim["exchanges"]


def test_get_logs_with_keyword(tools):
    logs = tools.get_logs(limit=10)
    assert len(logs) > 0
    assert {"time", "source", "msg"} <= set(logs[0])
    # 关键字过滤生效
    filtered = tools.get_logs(limit=10, keyword="connected")
    assert all("connected" in log["msg"].lower() for log in filtered)


def test_get_trades_compact(client, trading_tools):
    # 先成交一笔（SIM 即时撮合市价单）
    trading_tools.place_order(
        "rb2510.SHFE", "LONG", 1, price=0, order_type="MARKET"
    )
    time.sleep(0.5)
    trades = trading_tools.get_trades(vt_symbol="rb2510.SHFE", limit=5)
    assert len(trades) >= 1
    assert {"vt_tradeid", "direction", "price", "volume", "time"} <= set(trades[0])


def test_get_performance_summary(tools):
    result = tools.get_performance(hours=24)
    assert {"total_trades", "win_rate", "realized_pnl", "total_pnl"} <= set(result)
    assert len(result["trades"]) <= 10


def test_get_intraday_bars(client, tools):
    client.post(
        "/api/gateways/SIM/subscribe",
        json={"vt_symbols": ["IF2509.CFFEX"]},
    )
    # 等待至少一根 1m K线由 Tick 合成
    time.sleep(1.0)
    result = tools.get_intraday_bars("IF2509.CFFEX", "1m", limit=10)
    # 可能还没落盘完整一分钟，允许空但结构必须正确
    assert "count" in result
    if result["count"]:
        assert len(result["bars"][0]) == 6


def test_strategy_file_roundtrip(tools):
    files = tools.list_strategy_files()
    assert isinstance(files, list)
    if files:
        content = tools.read_strategy_file(files[0]["filename"])
        assert "code" in content and len(content["code"]) > 0


def test_reconcile_positions_shape(tools):
    rows = tools.reconcile_positions()
    assert isinstance(rows, list)
    for row in rows:
        assert {"strategy_name", "strategy_pos", "gateway_net_pos", "matched"} <= set(row)


def test_place_order_allowed_when_enabled(trading_tools):
    result = trading_tools.place_order(
        "IF2509.CFFEX", "LONG", 1, price=99999, offset="OPEN"
    )
    assert "vt_orderid" in result


def test_execute_tool_success(tools):
    output = execute_tool(tools, "get_accounts", {})
    data = json.loads(output)
    assert isinstance(data, list)


def test_execute_tool_unknown(tools):
    assert execute_tool(tools, "not_a_tool", {}).startswith("错误")


def test_execute_tool_gate_error_as_text(tools):
    output = execute_tool(
        tools, "place_order",
        {"vt_symbol": "IF2509.CFFEX", "direction": "LONG", "volume": 1},
    )
    assert output.startswith("错误")
    assert "AI 交易未启用" in output


def test_execute_tool_http_error_as_text(tools):
    output = execute_tool(tools, "get_tick", {"vt_symbol": "NOPE.CFFEX"})
    assert output.startswith("错误")


def test_backtest_summary(tools):
    tools.download_backtest_data(
        "rb2510.SHFE", "2026-07-01T09:00:00", "2026-07-05T15:00:00"
    )
    result = tools.run_backtest(
        class_name="MaCrossStrategy",
        vt_symbol="rb2510.SHFE",
        start="2026-07-02T09:00:00",
        end="2026-07-05T15:00:00",
        size=10,
        pricetick=1,
        setting={"fast_window": 5, "slow_window": 20},
    )
    assert "statistics" in result
    assert "total_return" in result["statistics"]
    assert len(result["sample_trades"]) <= 10
    # 不包含 daily_results（节省 token）
    assert "daily_results" not in result
