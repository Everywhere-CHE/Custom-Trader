"""将量化交易平台以工具形式暴露的 MCP 服务器（stdio 模式）。

独立运行方式（需要后端 REST 服务器已启动）：

    e:/123456/custom-trader/backend/.venv/Scripts/python.exe \
        e:/123456/custom-trader/backend/mcp_server/server.py

环境变量：
    QT_API_BASE       平台 REST 基础地址（默认 http://127.0.0.1:8000/api）
    AI_ALLOW_TRADING  设为 "true" 以启用 place_order / cancel_order

工具描述/参数结构的唯一数据源是 app.ai.tools.TOOL_SPECS ；
这里每个工具都是显式签名的转发器（ FastMCP 会根据这些签名校验参数）。
"""

import os
import sys
from pathlib import Path

# 允许在任意位置作为脚本运行时能够 `import app.*`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.ai.tools import TOOL_SPECS, TradingTools, execute_tool  # noqa: E402

mcp = FastMCP("custom-trader")

_tools = TradingTools(
    base_url=os.environ.get("QT_API_BASE", "http://127.0.0.1:8000/api"),
    allow_trading=os.environ.get("AI_ALLOW_TRADING", "false").lower() == "true",
)

_DESCRIPTIONS = {spec["name"]: spec["description"] for spec in TOOL_SPECS}


def _run(name: str, **arguments: object) -> str:
    return execute_tool(_tools, name, dict(arguments))


@mcp.tool(description=_DESCRIPTIONS["get_market_overview"])
def get_market_overview(gateway: str = "", limit: int = 30) -> str:
    return _run("get_market_overview", gateway=gateway, limit=limit)


@mcp.tool(description=_DESCRIPTIONS["get_tick"])
def get_tick(vt_symbol: str) -> str:
    return _run("get_tick", vt_symbol=vt_symbol)


@mcp.tool(description=_DESCRIPTIONS["get_bars"])
def get_bars(
    vt_symbol: str,
    interval: str = "1m",
    limit: int = 100,
    start: str | None = None,
    end: str | None = None,
) -> str:
    return _run(
        "get_bars",
        vt_symbol=vt_symbol,
        interval=interval,
        limit=limit,
        start=start,
        end=end,
    )


@mcp.tool(description=_DESCRIPTIONS["get_positions"])
def get_positions() -> str:
    return _run("get_positions")


@mcp.tool(description=_DESCRIPTIONS["get_accounts"])
def get_accounts() -> str:
    return _run("get_accounts")


@mcp.tool(description=_DESCRIPTIONS["get_orders"])
def get_orders(
    active_only: bool = False, vt_symbol: str = "", limit: int = 20
) -> str:
    return _run(
        "get_orders", active_only=active_only, vt_symbol=vt_symbol, limit=limit
    )


@mcp.tool(description=_DESCRIPTIONS["place_order"])
def place_order(
    vt_symbol: str,
    direction: str,
    volume: float,
    price: float = 0,
    offset: str = "OPEN",
    order_type: str = "LIMIT",
) -> str:
    return _run(
        "place_order",
        vt_symbol=vt_symbol,
        direction=direction,
        volume=volume,
        price=price,
        offset=offset,
        order_type=order_type,
    )


@mcp.tool(description=_DESCRIPTIONS["cancel_order"])
def cancel_order(vt_orderid: str) -> str:
    return _run("cancel_order", vt_orderid=vt_orderid)


@mcp.tool(description=_DESCRIPTIONS["list_strategies"])
def list_strategies() -> str:
    return _run("list_strategies")


@mcp.tool(description=_DESCRIPTIONS["list_strategy_classes"])
def list_strategy_classes() -> str:
    return _run("list_strategy_classes")


@mcp.tool(description=_DESCRIPTIONS["create_strategy"])
def create_strategy(
    class_name: str,
    name: str,
    vt_symbol: str,
    setting: dict | None = None,
) -> str:
    return _run(
        "create_strategy",
        class_name=class_name,
        name=name,
        vt_symbol=vt_symbol,
        setting=setting,
    )


@mcp.tool(description=_DESCRIPTIONS["control_strategy"])
def control_strategy(name: str, action: str) -> str:
    return _run("control_strategy", name=name, action=action)


@mcp.tool(description=_DESCRIPTIONS["write_strategy_file"])
def write_strategy_file(
    filename: str, code: str, overwrite: bool = False
) -> str:
    return _run(
        "write_strategy_file", filename=filename, code=code, overwrite=overwrite
    )


@mcp.tool(description=_DESCRIPTIONS["reload_strategies"])
def reload_strategies() -> str:
    return _run("reload_strategies")


@mcp.tool(description=_DESCRIPTIONS["run_backtest"])
def run_backtest(
    class_name: str,
    vt_symbol: str,
    start: str,
    end: str | None = None,
    interval: str = "1m",
    capital: float = 1_000_000,
    rate: float = 0.0001,
    slippage: float = 0,
    size: float = 1,
    pricetick: float = 0.01,
    setting: dict | None = None,
) -> str:
    return _run(
        "run_backtest",
        class_name=class_name,
        vt_symbol=vt_symbol,
        start=start,
        end=end,
        interval=interval,
        capital=capital,
        rate=rate,
        slippage=slippage,
        size=size,
        pricetick=pricetick,
        setting=setting,
    )


@mcp.tool(description=_DESCRIPTIONS["download_backtest_data"])
def download_backtest_data(
    vt_symbol: str,
    start: str,
    end: str | None = None,
    interval: str = "1m",
    gateway_name: str = "SIM",
) -> str:
    return _run(
        "download_backtest_data",
        vt_symbol=vt_symbol,
        start=start,
        end=end,
        interval=interval,
        gateway_name=gateway_name,
    )


@mcp.tool(description=_DESCRIPTIONS["get_risk_settings"])
def get_risk_settings() -> str:
    return _run("get_risk_settings")


@mcp.tool(description=_DESCRIPTIONS["update_risk_settings"])
def update_risk_settings(updates: dict) -> str:
    return _run("update_risk_settings", updates=updates)


@mcp.tool(description=_DESCRIPTIONS["get_trades"])
def get_trades(vt_symbol: str = "", limit: int = 20) -> str:
    return _run("get_trades", vt_symbol=vt_symbol, limit=limit)


@mcp.tool(description=_DESCRIPTIONS["get_performance"])
def get_performance(hours: int = 24) -> str:
    return _run("get_performance", hours=hours)


@mcp.tool(description=_DESCRIPTIONS["get_intraday_bars"])
def get_intraday_bars(
    vt_symbol: str, interval: str = "1m", limit: int = 120
) -> str:
    return _run(
        "get_intraday_bars", vt_symbol=vt_symbol, interval=interval, limit=limit
    )


@mcp.tool(description=_DESCRIPTIONS["get_gateways"])
def get_gateways() -> str:
    return _run("get_gateways")


@mcp.tool(description=_DESCRIPTIONS["get_logs"])
def get_logs(limit: int = 30, keyword: str = "") -> str:
    return _run("get_logs", limit=limit, keyword=keyword)


@mcp.tool(description=_DESCRIPTIONS["edit_strategy"])
def edit_strategy(name: str, setting: dict) -> str:
    return _run("edit_strategy", name=name, setting=setting)


@mcp.tool(description=_DESCRIPTIONS["list_strategy_files"])
def list_strategy_files() -> str:
    return _run("list_strategy_files")


@mcp.tool(description=_DESCRIPTIONS["read_strategy_file"])
def read_strategy_file(filename: str) -> str:
    return _run("read_strategy_file", filename=filename)


@mcp.tool(description=_DESCRIPTIONS["reconcile_positions"])
def reconcile_positions() -> str:
    return _run("reconcile_positions")


@mcp.tool(description=_DESCRIPTIONS["sync_strategy_pos"])
def sync_strategy_pos(name: str) -> str:
    return _run("sync_strategy_pos", name=name)


@mcp.tool(description=_DESCRIPTIONS["get_key_levels"])
def get_key_levels(vt_symbol: str) -> str:
    return _run("get_key_levels", vt_symbol=vt_symbol)


@mcp.tool(description=_DESCRIPTIONS["get_strategy_health"])
def get_strategy_health() -> str:
    return _run("get_strategy_health")


@mcp.tool(description=_DESCRIPTIONS["search_tickers"])
def search_tickers(
    q: str, asset_type: str = "", limit: int = 10
) -> str:
    return _run("search_tickers", q=q, asset_type=asset_type, limit=limit)


@mcp.tool(description=_DESCRIPTIONS["get_capital_flow"])
def get_capital_flow(symbol: str) -> str:
    return _run("get_capital_flow", symbol=symbol)


@mcp.tool(description=_DESCRIPTIONS["get_capital_flow_history"])
def get_capital_flow_history(
    symbol: str, interval: str = "1d", start: str = "", end: str = ""
) -> str:
    return _run(
        "get_capital_flow_history",
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
    )


@mcp.tool(description=_DESCRIPTIONS["get_dragon_tiger"])
def get_dragon_tiger(date: str = "", board_type: str = "all") -> str:
    return _run("get_dragon_tiger", date=date, board_type=board_type)


@mcp.tool(description=_DESCRIPTIONS["get_financials"])
def get_financials(
    symbol: str, statement: str = "income", period: str = "annual", limit: int = 4
) -> str:
    return _run(
        "get_financials",
        symbol=symbol,
        statement=statement,
        period=period,
        limit=limit,
    )


@mcp.tool(description=_DESCRIPTIONS["get_financial_indicators"])
def get_financial_indicators(symbol: str, report: str = "") -> str:
    return _run("get_financial_indicators", symbol=symbol, report=report)


@mcp.tool(description=_DESCRIPTIONS["get_futures_main_contracts"])
def get_futures_main_contracts() -> str:
    return _run("get_futures_main_contracts")


@mcp.tool(description=_DESCRIPTIONS["get_futures_warehouse_receipts"])
def get_futures_warehouse_receipts(
    symbol: str, start_date: str, end_date: str
) -> str:
    return _run(
        "get_futures_warehouse_receipts",
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )


@mcp.tool(description=_DESCRIPTIONS["get_futures_basis"])
def get_futures_basis(symbol: str = "") -> str:
    return _run("get_futures_basis", symbol=symbol)


@mcp.tool(description=_DESCRIPTIONS["get_futures_positions_variety"])
def get_futures_positions_variety(date: str = "") -> str:
    return _run("get_futures_positions_variety", date=date)


@mcp.tool(description=_DESCRIPTIONS["get_market_regime"])
def get_market_regime() -> str:
    return _run("get_market_regime")


@mcp.tool(description=_DESCRIPTIONS["get_monitor_alerts"])
def get_monitor_alerts(limit: int = 20) -> str:
    return _run("get_monitor_alerts", limit=limit)


@mcp.tool(description=_DESCRIPTIONS["get_paper_overview"])
def get_paper_overview() -> str:
    return _run("get_paper_overview")


@mcp.tool(description=_DESCRIPTIONS["get_market_phase"])
def get_market_phase() -> str:
    return _run("get_market_phase")


@mcp.tool(description=_DESCRIPTIONS["get_abnormal_moves"])
def get_abnormal_moves() -> str:
    return _run("get_abnormal_moves")


@mcp.tool(description=_DESCRIPTIONS["run_screener"])
def run_screener(strategies: list[str] | None = None) -> str:
    return _run("run_screener", strategies=strategies)


# ----- 期权（fuyao 行情 + 本地合约目录） -----


@mcp.tool(description=_DESCRIPTIONS["get_options_varieties"])
def get_options_varieties() -> str:
    return _run("get_options_varieties")


@mcp.tool(description=_DESCRIPTIONS["search_option_contracts"])
def search_option_contracts(
    underlying: str,
    option_type: str = "",
    expiry: str = "",
) -> str:
    return _run(
        "search_option_contracts",
        underlying=underlying,
        option_type=option_type,
        expiry=expiry,
    )


@mcp.tool(description=_DESCRIPTIONS["get_option_daily"])
def get_option_daily(thscode: str, start: str = "", end: str = "") -> str:
    return _run(
        "get_option_daily",
        thscode=thscode,
        start=start,
        end=end,
    )


@mcp.tool(description=_DESCRIPTIONS["get_option_chain"])
def get_option_chain(underlying: str, expiry: str) -> str:
    return _run(
        "get_option_chain",
        underlying=underlying,
        expiry=expiry,
    )


if __name__ == "__main__":
    mcp.run()
