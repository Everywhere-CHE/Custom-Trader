# MCP（Model Context Protocol）接入指南

CustomTrader 内置一个 **MCP 服务器**（stdio 传输），把平台的行情、持仓、
回测、期权链、监控等 **50 个工具** 暴露给任意支持 MCP 的 AI 客户端
（Claude Desktop、ZCode、Cline 等），让 AI 直接查询与操作你的交易终端。

## 前置条件

1. **平台本体已运行**（桌面版双击启动，或服务器上 `./start.sh --prod`）。
   MCP 的每个工具都是对平台 REST API 的一次调用，平台不在线时全部工具不可用。
2. MCP 客户端与平台**在同一台机器**（默认 `127.0.0.1:8000`）；远程接入见下文
   [远程接入](#远程接入)。
3. 可选：设置页填入 fuyao API Key（解锁龙虎榜/资金流/期权链等增强数据工具）。

## 启动 MCP 服务器

```bash
# 在源码目录（backend 同级）执行；使用后端 venv 的 Python
backend/.venv/bin/python mcp_server/server.py
```

MCP 服务器使用 **stdio** 传输——由 MCP 客户端拉起并托管，通常不需要手动运行。

## 客户端配置示例

### Claude Desktop

编辑 `claude_desktop_config.json`（菜单：File → Settings → Developer →
Edit Config）：

```json
{
  "mcpServers": {
    "custom-trader": {
      "command": "/path/to/custom-trader/backend/.venv/bin/python",
      "args": ["/path/to/custom-trader/backend/mcp_server/server.py"],
      "env": {
        "QT_API_BASE": "http://127.0.0.1:8000/api",
        "AI_ALLOW_TRADING": "false"
      }
    }
  }
}
```

### 其它 MCP 客户端（ZCode / Cline / 任意支持 stdio 的客户端）

- **command**：后端 venv 的 Python 绝对路径
- **args**：`mcp_server/server.py` 的绝对路径
- **env**：见下表

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QT_API_BASE` | `http://127.0.0.1:8000/api` | 平台 REST API 地址 |
| `AI_ALLOW_TRADING` | `false` | 设为 `true` 解锁 `place_order` / `cancel_order`（**AI 将能真实下单，请确认风控设置后再开启**） |

## 工具清单（50 个）

| 分类 | 工具（节选） |
|---|---|
| 行情 | get_market_overview / get_tick / get_bars / get_intraday_bars |
| 持仓交易 | get_positions / get_accounts / get_orders / get_trades / place_order* / cancel_order* |
| 回测与优化 | run_backtest / run_optimize / download_backtest_data / get_performance |
| 策略 | get_strategies / create_strategy / start_strategy / stop_strategy / reload_strategies |
| 期权 | get_options_varieties / search_option_contracts / get_option_chain / get_option_daily |
| 增强数据（需 fuyao key） | get_capital_flow / get_dragon_tiger / get_financials / get_futures_basis / search_tickers |
| 市场分析 | get_market_regime / get_market_phase / get_abnormal_moves / run_screener |
| 风控与守护 | get_risk_status / update_risk_settings / get_guardian_status / get_monitor_alerts |

\* 带星号的交易类工具默认**禁用**：调用会返回「AI 交易未启用」。开启方法：
设置 `AI_ALLOW_TRADING=true` 并确认你理解风险（AI 可真实下单）。

## 远程接入

MCP 工具默认直连 `127.0.0.1:8000`（与平台同机，免鉴权）。若 MCP 客户端与
平台不在同一台机器，推荐 **SSH 隧道**（平台 API 有登录鉴权，隧道保持本机
语义最简单）：

```bash
ssh -N -L 8000:127.0.0.1:8000 user@your-server
# 然后本机 QT_API_BASE 用默认值即可
```

> 直接把 `QT_API_BASE` 指向远程 `https://your-server/api` 需要处理平台登录
> 鉴权（Bearer token），当前版本的 MCP 工具不携带 token——远程场景请走隧道。

## 排错

| 现象 | 原因与处理 |
|---|---|
| 工具返回「连接被拒绝」 | 平台未启动——先启动 CustomTrader |
| 工具返回「未配置 FUYAO API Key」 | 设置页 → FUYAO 数据源 → 填 Key（增强数据工具需要） |
| 工具返回「AI 交易未启用」 | 交易门控生效（默认关闭）——见上文环境变量 |
| 工具返回 429 / tpm 限流 | 模型或数据服务限流，稍后重试 |
| Claude Desktop 里看不到工具 | 检查 config JSON 的路径/Python 是否为 venv 内的解释器，重启客户端 |
