"""共享交易工具层。

同一套实现同时被 MCP 服务器（独立进程）和应用内的 AIEngine 使用：
每个工具都是对平台 REST API 的一次 HTTP 调用，因此无论调用方是谁，
行为都完全一致。

``TOOL_SPECS`` 是工具 schema 的唯一真实来源：MCP 服务器和
Anthropic tool-use 循环都从中注册。

输出是紧凑的、对 LLM 友好的摘要（不含完整的五档深度，也不含
完整的 daily_results 数组）。
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..core.utility import CHINA_TZ

# 可通过环境变量 QT_API_BASE 覆盖，默认 127.0.0.1:8000
DEFAULT_BASE_URL = os.environ.get("QT_API_BASE", "http://127.0.0.1:8000/api")

# 同花顺金融数据 API（fuyao）—— 资金流/龙虎榜/财务等特色数据源
FUYAO_BASE_URL = "https://fuyao.aicubes.cn"
_FUYAO_ERROR_HINTS: dict[int, str] = {
    1001: "缺少必填参数",
    1002: "参数格式错误",
    1003: "参数越界",
    2001: "API Key 无效",
    2003: "无权访问（该能力可能未开放外部接入）",
    2004: "该数据为同花顺AI客户端专用，未开放 API（可告知用户改用其它工具）",
    3001: "标的不存在",
    4001: "触发限流，请稍后重试",
    5001: "服务端错误",
}


class ToolExecutionError(Exception):
    """工具级错误，其消息旨在供 LLM 阅读。"""


class TradingTools:
    """基于平台 REST API 的同步工具实现。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        allow_trading: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=120)
        self.allow_trading = allow_trading

    # ------------------------------------------------------------------
    # HTTP 辅助方法
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = self.client.get(f"{self.base_url}{path}", params=params)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: dict | None = None) -> Any:
        resp = self.client.post(f"{self.base_url}{path}", json=body or {})
        self._raise_for_status(resp)
        return resp.json()

    def _put(self, path: str, body: dict) -> Any:
        resp = self.client.put(f"{self.base_url}{path}", json=body)
        self._raise_for_status(resp)
        return resp.json()

    def _delete(self, path: str) -> Any:
        resp = self.client.delete(f"{self.base_url}{path}")
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ToolExecutionError(f"接口错误 {resp.status_code}: {detail}")

    def _check_trading_allowed(self) -> None:
        if not self.allow_trading:
            raise ToolExecutionError(
                "AI 交易未启用（AI_ALLOW_TRADING=false）。只能提供分析和建议，"
                "不能直接下单/撤单。用户可在 backend/.env 中开启。"
            )

    # ------------------------------------------------------------------
    # 只读：行情 / 持仓账户
    # ------------------------------------------------------------------

    def get_market_overview(self, gateway: str = "", limit: int = 30) -> dict:
        contracts = self._get("/contracts", {"gateway": gateway} if gateway else None)
        ticks = {t["vt_symbol"]: t for t in self._get("/ticks")}
        rows = []
        for c in contracts[: max(1, limit)]:
            tick = ticks.get(c["vt_symbol"])
            last = tick["last_price"] if tick else 0
            pre = tick["pre_close"] if tick else 0
            chg = round((last - pre) / pre * 100, 2) if pre else 0
            rows.append(
                {
                    "vt_symbol": c["vt_symbol"],
                    "name": c["name"],
                    "product": c["product"],
                    "last": last,
                    "chg_pct": chg,
                    "volume": tick["volume"] if tick else 0,
                }
            )
        return {"count": len(rows), "contracts": rows}

    def get_tick(self, vt_symbol: str) -> dict:
        t = self._get(f"/ticks/{vt_symbol}")
        return {
            "vt_symbol": t["vt_symbol"],
            "name": t["name"],
            "time": t["datetime"],
            "last": t["last_price"],
            "open": t["open_price"],
            "high": t["high_price"],
            "low": t["low_price"],
            "pre_close": t["pre_close"],
            "volume": t["volume"],
            "bid1": t["bid_price_1"],
            "bid1_vol": t["bid_volume_1"],
            "ask1": t["ask_price_1"],
            "ask1_vol": t["ask_volume_1"],
            "limit_up": t["limit_up"],
            "limit_down": t["limit_down"],
        }

    def get_bars(
        self,
        vt_symbol: str,
        interval: str = "1m",
        limit: int = 100,
        start: str | None = None,
        end: str | None = None,
    ) -> dict:
        params: dict = {"vt_symbol": vt_symbol, "interval": interval}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        bars = self._get("/bars", params)
        if not bars:
            return {"count": 0, "bars": [], "hint": "无数据；可先调用 download_backtest_data"}
        tail = bars[-max(1, limit):]
        return {
            "count": len(bars),
            "first": bars[0]["datetime"],
            "last": bars[-1]["datetime"],
            "returned": len(tail),
            "columns": ["datetime", "open", "high", "low", "close", "volume"],
            "bars": [
                [
                    b["datetime"],
                    b["open_price"],
                    b["high_price"],
                    b["low_price"],
                    b["close_price"],
                    b["volume"],
                ]
                for b in tail
            ],
        }

    def get_key_levels(self, vt_symbol: str) -> dict:
        """9 大类关键价位：筹码密集区/枢轴点/前高前低/布林/Keltner/ATR/缺口/Fib/整数关口。"""
        try:
            data = self._get("/key-levels", {"vt_symbol": vt_symbol})
        except Exception as e:  # noqa: BLE001 —— 转成模型可读的提示
            return {"vt_symbol": vt_symbol, "error": str(e)}
        ref = data.get("reference") or {}
        return {
            "vt_symbol": vt_symbol,
            "close": ref.get("close"),
            "atr": ref.get("atr"),
            "support_count": ref.get("support_count", 0),
            "resistance_count": ref.get("resistance_count", 0),
            "levels": [
                {
                    "value": lv["value"],
                    "label": lv["label"],
                    "category": lv["type"],
                    "side": lv["side"],
                    "strength": lv["strength"],
                    "distance_pct": lv["distance_pct"],
                }
                for lv in data.get("levels", [])
            ],
            "hint": "side=pivot 表示紧贴现价的中枢价位；distance_pct 为相对现价百分比",
        }

    def get_strategy_health(self) -> dict:
        """策略守护档案总览：基线、最近复验与三态状态。"""
        try:
            data = self._get("/guardian")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        guardians = []
        for g in data.get("guardians", []):
            checks = g.get("checks") or []
            last = checks[-1] if checks else None
            guardians.append(
                {
                    "guardian_id": g.get("guardian_id"),
                    "class_name": g.get("class_name"),
                    "vt_symbol": g.get("vt_symbol"),
                    "setting": g.get("setting"),
                    "status": g.get("status"),
                    "consecutive_watch": g.get("consecutive_watch", 0),
                    "baseline": {
                        "range": [g["baseline"].get("start"), g["baseline"].get("end")]
                        if g.get("baseline")
                        else None,
                        "score": g["baseline"].get("score") if g.get("baseline") else None,
                        "metric": g.get("metric"),
                    },
                    "last_check": (
                        {
                            "checked_at": last.get("checked_at"),
                            "new_bars": last.get("new_bars"),
                            "robust_score": last.get("robust_score"),
                            "pct_degradation": last.get("pct_degradation"),
                            "trade_count": last.get("trade_count"),
                            "status_after": last.get("status_after"),
                            "reasons": last.get("reasons"),
                            "error": last.get("error"),
                        }
                        if last
                        else None
                    ),
                }
            )
        return {
            "count": len(guardians),
            "guardians": guardians,
            "hint": "status: healthy=健康 / watch=观察(退化超标) / retired=退役(建议停用)；"
            "复验只在基线之后的新数据上进行",
        }

    def get_market_regime(self) -> dict:
        """市场环境：自选池 4 维加权 5 档（强势/偏强/震荡/偏弱/弱势）。"""
        try:
            data = self._get("/regime")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        if data.get("status") != "ok":
            return {"status": data.get("status"), "hint": data.get("hint")}
        return {
            "as_of": data.get("as_of"),
            "score": data.get("score"),
            "label": data.get("label"),
            "dimensions": data.get("dimensions"),
            "universe_size": data.get("universe_size"),
            "recent_history": [
                {"date": h["date"], "score": h["score"], "label": h["label"]}
                for h in (data.get("history") or [])[-10:]
            ],
            "note": data.get("note"),
            "hint": "维度 trend=站上MA20占比 / breadth=近5日上涨占比 / momentum=平均5日涨跌 / volatility=波动(高分=低波动)",
        }

    def get_market_phase(self) -> dict:
        """市场情绪：连板梯队 + 情绪周期 6 阶段。"""
        try:
            data = self._get("/market-phase")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        if data.get("status") != "ok":
            return {"status": data.get("status"), "hint": data.get("hint")}
        return {
            "as_of": data.get("as_of"),
            "phase": data.get("phase"),
            "phase_sequence": data.get("phase_sequence"),
            "ladder_stats": data.get("ladder_stats"),
            "ladder_top": data.get("ladder_top"),
            "note": data.get("note"),
            "hint": "phase 含义：冰点(情绪冰点谨慎)/启动(回暖)/主升(赚钱效应扩散)/高潮(过热防分歧)/退潮(亏钱效应)/修复(尝试回暖)；promotion_rate=昨日涨停今日续板比例",
        }

    def get_abnormal_moves(self) -> dict:
        """异动监控：交易所口径偏离预警。"""
        try:
            data = self._get("/abnormal")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        if data.get("status") != "ok":
            return {"status": data.get("status"), "hint": data.get("hint")}
        return {
            "as_of": data.get("as_of"),
            "baseline3_pct": data.get("baseline3_pct"),
            "counts": data.get("counts"),
            "items": [
                {
                    "vt_symbol": it["vt_symbol"],
                    "name": it["name"],
                    "board": it["board"],
                    "ret3_pct": it["ret3_pct"],
                    "dev3_pct": it["dev3_pct"],
                    "closeness": it["closeness"],
                    "level": it["level"],
                    "ret10_pct": it.get("ret10_pct"),
                    "ret30_pct": it.get("ret30_pct"),
                }
                for it in data.get("items", [])
            ],
            "hint": "closeness=偏离/阈值：≥1.0 触发交易所异常波动口径、≥0.7 边缘、≥0.5 关注；level 后缀 涨/跌 表方向",
        }

    def run_screener(self, strategies: list[str] | None = None) -> dict:
        """全市场策略扫描。"""
        try:
            data = self._post("/screener/run", {"strategies": strategies or []})
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        if data.get("status") != "ok":
            return {"status": data.get("status"), "hint": data.get("hint")}
        return {
            "as_of": data.get("as_of"),
            "universe_size": data.get("universe_size"),
            "results": [
                {
                    "strategy": s["name"],
                    "count": s["count"],
                    "hits": [
                        f"{it['vt_symbol']} {it['name']}（{it['detail']}）"
                        for it in s["items"][:20]
                    ],
                }
                for s in data.get("strategies", [])
            ],
        }

    def get_monitor_alerts(self, limit: int = 20) -> dict:
        """监控中心告警记录（最新在前）与规则概览。"""
        try:
            alerts = self._get("/monitor/alerts", {"limit": max(1, min(int(limit), 100))})
            rules = self._get("/monitor/rules")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        return {
            "rules": [
                {
                    "rule_id": r.get("rule_id"),
                    "name": r.get("name"),
                    "enabled": r.get("enabled"),
                    "scope": r.get("scope", {}).get("type"),
                    "last_triggered_at": r.get("last_triggered_at"),
                }
                for r in rules.get("rules", [])
            ],
            "alerts": [
                {
                    "time": a.get("time"),
                    "rule_name": a.get("rule_name"),
                    "vt_symbol": a.get("vt_symbol"),
                    "message": a.get("message"),
                }
                for a in alerts.get("alerts", [])
            ],
        }

    def get_paper_overview(self) -> dict:
        """模拟盘总览：权益/持仓/已实现与浮动盈亏（台账重放）。"""
        try:
            data = self._get("/paper/overview")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        return {
            "capital": data.get("settings", {}).get("capital"),
            "equity": data.get("equity"),
            "cash": data.get("cash"),
            "total_return_pct": data.get("total_return_pct"),
            "realized_pnl": data.get("realized_pnl"),
            "unrealized_pnl": data.get("unrealized_pnl"),
            "fees_total": data.get("fees_total"),
            "trade_count": data.get("trade_count"),
            "positions": data.get("positions"),
            "hint": "模拟盘为净持仓模型，市价单按最新快照价即时成交",
        }

    def get_positions(self) -> list[dict]:
        return [
            {
                "vt_symbol": p["vt_symbol"],
                "direction": p["direction"],
                "volume": p["volume"],
                "price": p["price"],
                "pnl": p["pnl"],
                "yd_volume": p["yd_volume"],
            }
            for p in self._get("/positions")
            if p["volume"]
        ]

    def get_accounts(self) -> list[dict]:
        return [
            {
                "accountid": a["vt_accountid"],
                "balance": a["balance"],
                "available": a["available"],
                "frozen": a["frozen"],
            }
            for a in self._get("/accounts")
        ]

    def get_orders(
        self, active_only: bool = False, vt_symbol: str = "", limit: int = 20
    ) -> list[dict]:
        params: dict = {"active_only": active_only}
        if vt_symbol:
            params["vt_symbol"] = vt_symbol
        orders = self._get("/orders", params)
        orders.sort(key=lambda o: o.get("datetime") or "", reverse=True)
        return [
            {
                "vt_orderid": o["vt_orderid"],
                "vt_symbol": o["vt_symbol"],
                "direction": o["direction"],
                "offset": o["offset"],
                "price": o["price"],
                "volume": o["volume"],
                "traded": o["traded"],
                "status": o["status"],
                "time": o["datetime"],
            }
            for o in orders[: max(1, limit)]
        ]

    def get_trades(self, vt_symbol: str = "", limit: int = 20) -> list[dict]:
        params: dict = {}
        if vt_symbol:
            params["vt_symbol"] = vt_symbol
        trades = self._get("/trades", params)
        # 接口已按时间倒序返回（最新在前）
        return [
            {
                "vt_tradeid": t["vt_tradeid"],
                "vt_symbol": t["vt_symbol"],
                "direction": t["direction"],
                "offset": t["offset"],
                "price": t["price"],
                "volume": t["volume"],
                "pnl": t.get("pnl", 0),
                "fee": t.get("fee", 0),
                "time": t["datetime"],
            }
            for t in trades[: max(1, limit)]
        ]

    def get_performance(self, hours: int = 24) -> dict:
        """最近 N 小时的交易绩效摘要（胜率/盈亏/手续费/持仓浮盈）。"""
        result = self._get("/backtest/performance", {"hours": hours})
        # 明细行数较多时截断，只保留摘要 + 最近 10 笔
        result["trades"] = result.get("trades", [])[:10]
        return result

    def get_intraday_bars(
        self, vt_symbol: str, interval: str = "1m", limit: int = 120
    ) -> dict:
        """盘中实时K线（由真实收到的 Tick 合成；1h/d/w 会拼接历史）。"""
        bars = self._get(
            "/bars/intraday",
            {"vt_symbol": vt_symbol, "interval": interval, "limit": limit},
        )
        if not bars:
            return {
                "count": 0,
                "bars": [],
                "hint": "无盘中数据；请确认该合约已订阅且有行情推送",
            }
        return {
            "count": len(bars),
            "columns": ["datetime", "open", "high", "low", "close", "volume"],
            "bars": [
                [
                    b["datetime"],
                    b["open_price"],
                    b["high_price"],
                    b["low_price"],
                    b["close_price"],
                    b["volume"],
                ]
                for b in bars[-max(1, limit):]
            ],
        }

    def get_gateways(self) -> list[dict]:
        """各网关的连接状态与支持的交易所。"""
        return [
            {
                "name": g["name"],
                "connected": g["connected"],
                "exchanges": g["exchanges"],
            }
            for g in self._get("/gateways")
        ]

    def get_logs(self, limit: int = 30, keyword: str = "") -> list[dict]:
        """最近的系统日志（可按关键字过滤），用于排查连接/风控/策略问题。"""
        logs = self._get("/logs", {"limit": max(limit, 100)})
        if keyword:
            logs = [log for log in logs if keyword.lower() in log["msg"].lower()]
        return [
            {"time": log["time"], "source": log["gateway_name"], "msg": log["msg"]}
            for log in logs[: max(1, limit)]
        ]

    # ------------------------------------------------------------------
    # 交易（受限）
    # ------------------------------------------------------------------

    def place_order(
        self,
        vt_symbol: str,
        direction: str,
        volume: float,
        price: float = 0,
        offset: str = "OPEN",
        order_type: str = "LIMIT",
    ) -> dict:
        self._check_trading_allowed()
        return self._post(
            "/orders",
            {
                "vt_symbol": vt_symbol,
                "direction": direction,
                "offset": offset,
                "type": order_type,
                "price": price,
                "volume": volume,
            },
        )

    def cancel_order(self, vt_orderid: str) -> dict:
        """撤销委托（仅撤单，不开新仓，风险较低，不受 AI_ALLOW_TRADING 限制）。"""
        return self._delete(f"/orders/{vt_orderid}")

    # ------------------------------------------------------------------
    # 策略
    # ------------------------------------------------------------------

    def list_strategies(self) -> list[dict]:
        return [
            {
                "name": s["strategy_name"],
                "class_name": s["class_name"],
                "vt_symbol": s["vt_symbol"],
                "inited": s["variables"].get("inited"),
                "trading": s["variables"].get("trading"),
                "pos": s["variables"].get("pos"),
                "parameters": s["parameters"],
            }
            for s in self._get("/strategies")
        ]

    def list_strategy_classes(self) -> list[dict]:
        return self._get("/strategies/classes")

    def create_strategy(
        self,
        class_name: str,
        name: str,
        vt_symbol: str,
        setting: dict | None = None,
    ) -> dict:
        return self._post(
            "/strategies",
            {
                "class_name": class_name,
                "name": name,
                "vt_symbol": vt_symbol,
                "setting": setting or {},
            },
        )

    def control_strategy(self, name: str, action: str) -> dict:
        if action not in ("init", "start", "stop"):
            raise ToolExecutionError(
                f"未知操作 {action!r}，必须是 init/start/stop"
            )
        return self._post(f"/strategies/{name}/{action}")

    def edit_strategy(self, name: str, setting: dict) -> dict:
        """修改策略实例参数（运行中的策略需先停止才生效于下一根K线）。"""
        return self._put(f"/strategies/{name}", {"setting": setting})

    def read_strategy_file(self, filename: str) -> dict:
        """读取用户策略目录中某个 .py 文件的完整源码。"""
        return self._get(f"/strategies/files/{filename}")

    def list_strategy_files(self) -> list[dict]:
        """列出用户策略目录下的全部 .py 文件。"""
        return self._get("/strategies/files")

    def reconcile_positions(self) -> list[dict]:
        """策略 pos 与网关净持仓对账，返回每个策略的差异行。"""
        return self._get("/strategies/reconcile")

    def sync_strategy_pos(self, name: str) -> dict:
        """把策略 pos 同步为网关净持仓（需策略已停止且合约未被共用）。"""
        self._check_trading_allowed()
        return self._post(f"/strategies/{name}/sync-pos")

    def write_strategy_file(
        self, filename: str, code: str, overwrite: bool = False
    ) -> dict:
        return self._post(
            "/strategies/files",
            {"filename": filename, "code": code, "overwrite": overwrite},
        )

    def reload_strategies(self) -> dict:
        return self._post("/strategies/reload")

    # ------------------------------------------------------------------
    # 回测
    # ------------------------------------------------------------------

    def run_backtest(
        self,
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
    ) -> dict:
        body: dict = {
            "class_name": class_name,
            "vt_symbol": vt_symbol,
            "interval": interval,
            "start": start,
            "capital": capital,
            "rate": rate,
            "slippage": slippage,
            "size": size,
            "pricetick": pricetick,
            "setting": setting or {},
        }
        if end:
            body["end"] = end
        result = self._post("/backtest", body)
        trades = result.get("trades", [])
        # 该 API 以列表形式返回统计（每个 策略 x 合约 组合一条）；
        # 本工具只运行单个组合，因此为模型将其扁平化。
        stats = result.get("statistics", [])
        if isinstance(stats, list):
            stats = stats[0] if stats else {}
        return {
            "statistics": stats,
            "trade_count": len(trades),
            "sample_trades": [
                {
                    "time": t["datetime"],
                    "direction": t["direction"],
                    "offset": t["offset"],
                    "price": t["price"],
                    "volume": t["volume"],
                }
                for t in trades[:10]
            ],
        }

    def download_backtest_data(
        self,
        vt_symbol: str,
        start: str,
        end: str | None = None,
        interval: str = "1m",
        gateway_name: str = "SIM",
    ) -> dict:
        body: dict = {
            "vt_symbol": vt_symbol,
            "interval": interval,
            "start": start,
            "gateway_name": gateway_name,
        }
        if end:
            body["end"] = end
        return self._post("/backtest/download-data", body)

    # ------------------------------------------------------------------
    # 风控
    # ------------------------------------------------------------------

    def get_risk_settings(self) -> dict:
        return self._get("/risk")

    def update_risk_settings(self, updates: dict) -> dict:
        return self._put("/risk", updates)

    # ------------------------------------------------------------------
    # 同花顺金融数据（fuyao）：特色数据源
    # ------------------------------------------------------------------

    _EXCHANGE_SUFFIX = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ",
                        "SH": "SH", "SZ": "SZ", "BJ": "BJ",
                        # 期货：CTP 交易所代码 -> fuyao thscode 后缀
                        "SHFE": "SHF", "CFFEX": "CFE", "CZCE": "CZC",
                        "DCE": "DCE", "INE": "INE", "GFEX": "GFE",
                        "SHF": "SHF", "CFE": "CFE"}

    def _thscode(self, symbol: str) -> str:
        """把 600519.SSE / 600519.SH / rb2701.SHFE / RB2701.SHF
        统一为同花顺 thscode。"""
        s = symbol.strip().upper()
        if "." in s:
            prefix, suffix = s.split(".", 1)
            return f"{prefix}.{self._EXCHANGE_SUFFIX.get(suffix, suffix)}"
        # 纯 6 位代码：按 A 股段位推断交易所
        if s.isdigit() and len(s) == 6:
            if s.startswith("6"):
                return f"{s}.SH"
            if s.startswith(("0", "3")):
                return f"{s}.SZ"
            if s.startswith(("4", "8", "9")):
                return f"{s}.BJ"
        return s

    def _fuyao_get(self, path: str, params: dict | None = None) -> dict:
        """fuyao GET 请求：统一信封处理，错误转为 LLM 可读文本。"""
        # 延迟导入避免循环依赖；Key 与网关配置同源（data/fuyao_config.json）
        from ..config import get_settings
        from ..fuyao_config import load_fuyao_config

        api_key = str(load_fuyao_config(get_settings()).get("api_key") or "").strip()
        if not api_key:
            raise ToolExecutionError(
                "未配置 FUYAO API Key（设置 → 网关管理 → FUYAO 数据源设置）"
            )
        try:
            resp = self.client.get(
                f"{FUYAO_BASE_URL}{path}",
                params=params or {},
                headers={"X-api-key": api_key},
            )
        except httpx.HTTPError as e:
            raise ToolExecutionError(f"无法访问同花顺数据服务 - {e}") from None
        if resp.status_code >= 400:
            raise ToolExecutionError(
                f"fuyao 接口 HTTP {resp.status_code}: {resp.text[:200]}"
            )
        payload = resp.json()
        code = payload.get("code", -1)
        if code != 0:
            hint = _FUYAO_ERROR_HINTS.get(int(code), "")
            raise ToolExecutionError(
                f"fuyao 业务错误 code={code} {payload.get('message', '')}"
                + (f"（{hint}）" if hint else "")
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _parse_ms(value: str, field: str) -> int:
        """把 '2026-10-01' / '2026-10-01 15:00' / ISO 转为毫秒时间戳。"""
        v = value.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y/%m/%d", "%Y%m%d"):
            try:
                return int(datetime.strptime(v, fmt).timestamp() * 1000)
            except ValueError:
                continue
        try:
            return int(datetime.fromisoformat(v).timestamp() * 1000)
        except ValueError:
            raise ToolExecutionError(
                f"参数 {field} 时间格式无法解析: {value!r}（示例 2026-10-01）"
            ) from None

    def search_tickers(self, q: str, asset_type: str = "",
                       limit: int = 10) -> dict:
        """跨市场标的检索（名称/代码 → thscode）。"""
        params: dict = {"q": q, "limit": max(1, min(50, int(limit)))}
        if asset_type:
            params["asset_type"] = asset_type
        data = self._fuyao_get("/api/meta/tickers/search", params)
        items = [
            {
                "thscode": it.get("thscode"),
                "name": it.get("name"),
                "exchange": it.get("exchange"),
                "asset_type": it.get("asset_type"),
                "list_date": it.get("list_date"),
            }
            for it in (data.get("item") or [])
        ]
        return {"count": len(items), "items": items}

    def get_capital_flow(self, symbol: str) -> dict:
        """单只 A 股主力资金实时快照（超大单/大单/中单/小单，单位元）。"""
        ths = self._thscode(symbol)
        data = self._fuyao_get(
            "/api/a-share/capital-flow/snapshot", {"thscode": ths}
        )
        return {"thscode": ths, **data}

    def get_capital_flow_history(self, symbol: str, interval: str = "1d",
                                 start: str = "", end: str = "") -> dict:
        """主力资金历史序列。1d 默认近90天；1m 默认最近1天（最长7天）。"""
        ths = self._thscode(symbol)
        iv = interval if interval in ("1m", "1d") else "1d"
        end_dt = (
            datetime.fromtimestamp(self._parse_ms(end, "end") / 1000)
            if end else datetime.now()
        )
        default_days = 1 if iv == "1m" else 90
        start_dt = (
            datetime.fromtimestamp(self._parse_ms(start, "start") / 1000)
            if start else end_dt - timedelta(days=default_days)
        )
        if iv == "1m" and (end_dt - start_dt).days > 7:
            start_dt = end_dt - timedelta(days=7)
        if iv == "1d" and (end_dt - start_dt).days > 365:
            start_dt = end_dt - timedelta(days=365)
        data = self._fuyao_get(
            "/api/a-share/capital-flow/historical",
            {"thscode": ths, "interval": iv,
             "start": int(start_dt.timestamp() * 1000),
             "end": int(end_dt.timestamp() * 1000)},
        )
        rows = []
        for r in (data.get("item") or []):
            row = [r.get("date_ms")]
            for cat in ("super_large", "large", "medium", "small"):
                cell = r.get(cat) or {}
                row.append(cell.get("inflow_amount"))
                row.append(cell.get("outflow_amount"))
            rows.append(row)
        return {
            "thscode": data.get("thscode") or ths,
            "interval": iv,
            "count": len(rows),
            "columns": ["date_ms", "超大单流入", "超大单流出", "大单流入",
                        "大单流出", "中单流入", "中单流出", "小单流入",
                        "小单流出"],
            "unit": "元",
            "rows": rows,
        }

    def get_dragon_tiger(self, date: str = "", board_type: str = "all") -> dict:
        """龙虎榜（默认最近交易日）。board_type: all/org/hot_money。"""
        bt = board_type if board_type in ("all", "org", "hot_money") else "all"
        params: dict = {"board_type": bt}
        if date:
            params["date"] = date.strip()
        data = self._fuyao_get(
            "/api/a-share/special-data/dragon-tiger-list", params
        )
        stocks = sorted(
            (data.get("stock_items") or []),
            key=lambda s: (s.get("net_value") or 0),
            reverse=True,
        )
        top = [
            {
                "thscode": s.get("thscode"),
                "name": s.get("name"),
                "change_pct": round((s.get("change") or 0) * 100, 2),
                "net_buy": s.get("net_value"),
                "buy": s.get("buy_value"),
                "sell": s.get("sell_value"),
                "org_net_buy": s.get("org_net_value"),
                "hot_rank": s.get("hot_rank"),
                "reason": s.get("limit_reason"),
                "range_days": s.get("range_days"),
            }
            for s in stocks[:15]
        ]
        hot_money = [
            {
                "name": h.get("name"),
                "net_buy": h.get("buying"),
                "stocks": [r.get("name") for r in (h.get("rows") or [])][:8],
            }
            for h in (data.get("hot_money_items") or [])[:10]
        ]
        return {
            "trade_date": data.get("trade_date"),
            "board_type": bt,
            "stock_count": data.get("stock_count"),
            "top_stocks": top,
            "hot_money": hot_money,
        }

    def get_financials(self, symbol: str, statement: str = "income",
                       period: str = "annual", limit: int = 4) -> dict:
        """三大财务报表（income 利润表 / balance 资产负债表 / cashflow
        现金流量表），金额单位元，null 表示未披露。"""
        paths = {
            "income": "/api/a-share/financials/income-statements",
            "balance": "/api/a-share/financials/balance-sheets",
            "cashflow": "/api/a-share/financials/cash-flow-statements",
        }
        path = paths.get(statement.strip().lower())
        if not path:
            raise ToolExecutionError(
                f"statement 仅支持 income/balance/cashflow，收到 {statement!r}"
            )
        ths = self._thscode(symbol)
        params: dict = {
            "thscode": ths,
            "period": period if period in ("annual", "quarterly") else "annual",
            "limit": max(1, min(20, int(limit))),
        }
        data = self._fuyao_get(path, params)
        return {
            "thscode": ths,
            "statement": statement,
            "period": params["period"],
            "count": len(data.get("item") or []),
            "items": data.get("item") or [],
        }

    def get_financial_indicators(self, symbol: str, report: str = "") -> dict:
        """五类财务指标（成长/盈利/偿债/营运/现金流）。
        report 格式 yyyy-1~4（如 2025-4 表示 2025 年报），留空=最近年报期。"""
        ths = self._thscode(symbol)
        rpt = report.strip()
        if not rpt:
            rpt = f"{datetime.now().year - 1}-4"
        else:
            parts = rpt.replace("Q", "-").replace("q", "-").split("-")
            if len(parts) != 2 or not parts[0].isdigit() \
                    or parts[1] not in ("1", "2", "3", "4"):
                raise ToolExecutionError(
                    f"report 格式应为 yyyy-1..4（如 2025-4），收到 {report!r}"
                )
            rpt = f"{parts[0]}-{parts[1]}"
        data = self._fuyao_get(
            "/api/a-share/financials/indicators",
            {"thscode": ths, "report": rpt},
        )
        flat: dict[str, Any] = {}
        for ability in (data.get("abilities") or []):
            for ind in (ability.get("indicators") or []):
                flat[ind.get("index_id", "")] = ind.get("value")
        return {
            "thscode": data.get("thscode") or ths,
            "report": data.get("report") or rpt,
            "indicators": flat,
            "note": "百分比类指标按百分数值表达（89.12 即 89.12%），null=未披露",
        }

    # ------------------------------------------------------------------
    # 同花顺金融数据（fuyao）：期货特色数据
    # ------------------------------------------------------------------

    def get_futures_main_contracts(self) -> dict:
        """期货主连与主力合约列表：各品种当前主力合约代码、保证金率、
        手续费率（回答"螺纹钢主力是哪个合约"这类问题）。"""
        cont = self._fuyao_get(
            "/api/futures/contracts/main-continuous-list", {}
        )
        main = self._fuyao_get("/api/futures/contracts/main-list", {})
        fields = ("thscode", "ticker", "name", "variety_name",
                  "exchange_code", "last_trade_date", "margin_rate")
        return {
            "main_continuous": [
                {k: it.get(k) for k in fields}
                for it in (cont.get("item") or [])
            ],
            "main_contracts": [
                {k: it.get(k) for k in fields}
                for it in (main.get("item") or [])
            ],
        }

    def get_futures_warehouse_receipts(self, symbol: str,
                                       start_date: str, end_date: str) -> dict:
        """期货历史仓单：仓单数量、增减量、折算手数（日期 yyyy-MM-dd）。"""
        ths = self._thscode(symbol)
        if not start_date or not end_date:
            raise ToolExecutionError("start_date / end_date 必填（yyyy-MM-dd）")
        data = self._fuyao_get(
            "/api/futures/warehouse-receipts/historical",
            {"thscode": ths, "start_date": start_date.strip(),
             "end_date": end_date.strip()},
        )
        return {
            "thscode": ths,
            "count": len(data.get("item") or []),
            "items": data.get("item") or [],
        }

    def get_futures_basis(self, symbol: str = "") -> dict:
        """期货基差：symbol 留空返回全部品种主连最新基差（现货价/期价/
        基差/基差率），传合约则返回该合约历史基差序列。"""
        if symbol.strip():
            ths = self._thscode(symbol)
            data = self._fuyao_get(
                "/api/futures/basis/historical", {"thscode": ths}
            )
            return {"thscode": ths, "count": len(data.get("item") or []),
                    "items": data.get("item") or []}
        data = self._fuyao_get("/api/futures/basis/main-continuous-latest", {})
        fields = ("thscode", "name", "variety_name", "spot_price",
                  "close_price", "settle_price", "close_basis",
                  "settle_basis", "close_basis_rate", "updated_at")
        items = [
            {k: it.get(k) for k in fields}
            for it in (data.get("item") or [])
        ]
        return {"count": len(items), "items": items}

    def get_futures_positions_variety(self, date: str = "") -> dict:
        """期货品种日持仓：全品种成交量/多单/空单/净持仓及变化、
        资金流向（date 空=今天，非交易日请传最近交易日 yyyy-MM-dd）。"""
        d = date.strip() or datetime.now().strftime("%Y-%m-%d")
        data = self._fuyao_get(
            "/api/futures/positions/variety-daily", {"date": d}
        )
        return {"date": d, "count": len(data.get("item") or []),
                "items": data.get("item") or []}

    # ----- 期权（本地目录 + fuyao 行情） -----

    def get_options_varieties(self) -> dict:
        """期权品种资料：交易所、合约乘数、手续费、交割方式
        （ETF 期权/股指期权/商品期权全量）。"""
        data = self._fuyao_get("/api/options/varieties/list", {})
        fields = ("variety_code", "name", "exchange_code",
                  "contract_multiplier", "transaction_fee",
                  "settlement_type", "tick_size")
        seen = set()
        items = []
        for it in data.get("item") or []:
            key = (it.get("variety_code"), it.get("exchange_code"))
            if key in seen:
                continue
            seen.add(key)
            items.append({k: it.get(k) for k in fields})
        return {"count": len(items), "items": items}

    def search_option_contracts(self, underlying: str,
                                option_type: str = "",
                                expiry: str = "") -> dict:
        """检索本地期权合约目录（按标的/类型/到期日过滤，含行权价）。

        underlying 传标的代码（如 510050、510300）或股指品种
        （IO/MO/HO）；option_type: call/put；expiry: yyyy-MM-dd
        （最后交易日）。目录仅含**在市**合约，需先在期权页同步。
        """
        from ..db.models import OptionContractModel, OptionVarietyModel
        from ..db.session import create_session

        u = underlying.strip()
        vid = u if not u.isdigit() else u + "O"
        session = create_session()
        try:
            vrow = (
                session.query(OptionVarietyModel.exchange_code)
                .filter(OptionVarietyModel.variety_code == vid)
                .first()
            )
            if vrow is None:
                raise ToolExecutionError(
                    f"品种 {vid} 未同步：请先在『期权』页执行数据同步"
                )
            q = session.query(OptionContractModel).filter(
                OptionContractModel.variety_code == vid,
                OptionContractModel.exchange_code == vrow[0],
            )
            if option_type.strip():
                q = q.filter(
                    OptionContractModel.option_type == option_type.strip()
                )
            if expiry.strip():
                q = q.filter(
                    OptionContractModel.last_trade_date == expiry.strip()
                )
            rows = (
                q.order_by(
                    OptionContractModel.last_trade_date,
                    OptionContractModel.strike_price,
                )
                .limit(200)
                .all()
            )
        finally:
            session.close()
        return {
            "variety": vid,
            "count": len(rows),
            "items": [
                {
                    "thscode": r.thscode,
                    "name": r.name,
                    "option_type": r.option_type,
                    "strike": r.strike_price,
                    "expiry": r.last_trade_date,
                    "list_date": r.list_date,
                    "trade_amount": r.trade_amount,
                }
                for r in rows
            ],
        }

    def get_option_daily(self, thscode: str, start: str = "",
                         end: str = "") -> dict:
        """期权合约日K（OHLC+量额，默认最近100根；start/end 可取
        完整生命周期历史，如上市首日起）。"""
        code = thscode.strip()
        if not code:
            raise ToolExecutionError("thscode 必填（如 10011425.SH / IO2612-C-4000.CFE）")
        params: dict = {"thscode": code}
        if start.strip():
            params["start"] = self._parse_ms(start, "start")
        if end.strip():
            params["end"] = self._parse_ms(end, "end")
        data = self._fuyao_get("/api/options/prices/daily", params)
        items = []
        for it in data.get("item") or []:
            ts = it.get("timestamp")
            items.append(
                {
                    # 期权日K时间戳 = 交易日当日北京零点
                    "date": datetime.fromtimestamp(
                        ts / 1000, tz=CHINA_TZ
                    ).strftime("%Y-%m-%d")
                    if ts
                    else None,
                    "open": it.get("open_price"),
                    "high": it.get("high_price"),
                    "low": it.get("low_price"),
                    "close": it.get("close_price"),
                    "volume": it.get("volume"),
                    "turnover": it.get("turnover"),
                }
            )
        return {"thscode": code, "count": len(items), "items": items}

    def get_option_chain(self, underlying: str, expiry: str) -> dict:
        """期权链 T 型截面：按行权价配对的认购/认沽最新收盘、
        隐含波动率与 Delta（本地库；需先同步并拉过行情）。"""
        from ..db.models import BarModel, OptionContractModel, OptionVarietyModel
        from ..db.session import create_session
        from ..services.option_pricing import bsm_greeks, implied_vol

        u = underlying.strip()
        vid = u if not u.isdigit() else u + "O"
        session = create_session()
        try:
            vrow = (
                session.query(OptionVarietyModel)
                .filter(OptionVarietyModel.variety_code == vid)
                .first()
            )
            if vrow is None:
                raise ToolExecutionError(
                    f"品种 {vid} 未同步：请先在『期权』页执行数据同步"
                )
            exch_code = vrow.exchange_code or ""
            contracts = (
                session.query(OptionContractModel)
                .filter(
                    OptionContractModel.variety_code == vid,
                    OptionContractModel.exchange_code == exch_code,
                    OptionContractModel.last_trade_date == expiry.strip(),
                )
                .all()
            )

            def _tail(symbol: str, limit: int = 1) -> list[float]:
                rows_ = (
                    session.query(BarModel.close_price)
                    .filter(
                        BarModel.symbol == symbol,
                        BarModel.exchange == exch_code,
                        BarModel.interval == "d",
                    )
                    .order_by(BarModel.datetime.desc())
                    .limit(limit)
                    .all()
                )
                return [r[0] for r in reversed(rows_) if r[0]]
        finally:
            session.close()
        if not contracts:
            raise ToolExecutionError(
                f"{vid} 到期日 {expiry} 无合约（检查日期格式 yyyy-MM-dd）"
            )

        # 标的收盘：标的日线尾部（ETF 以 6 位代码 + 交易所后缀存 bars）
        u_close = 0.0
        if u.isdigit():
            ul_symbol = u + (".SZ" if exch_code == "SZSE" else ".SH")
            session = create_session()
            try:
                closes = _tail(ul_symbol, limit=1)
            finally:
                session.close()
            if closes:
                u_close = closes[-1]
        rows: dict[float, dict] = {}
        now = datetime.now()
        t_years = max(
            (datetime.strptime(expiry.strip(), "%Y-%m-%d") - now).days, 0
        ) / 365.0
        session = create_session()
        try:
            for c in contracts:
                side = rows.setdefault(
                    c.strike_price or 0.0,
                    {"strike": c.strike_price or 0.0},
                )
                quote: dict = {"thscode": c.thscode, "name": c.name}
                tail = _tail(c.thscode, limit=1)
                if tail:
                    quote["close"] = tail[-1]
                    if u_close > 0 and t_years > 0:
                        iv = implied_vol(
                            tail[-1], u_close,
                            c.strike_price or 0.0, t_years, 0.02,
                            c.option_type or "call",
                        )
                        if iv:
                            quote["iv"] = round(iv, 4)
                            g = bsm_greeks(
                                u_close, c.strike_price or 0.0, t_years,
                                0.02, iv, c.option_type or "call",
                            )
                            quote["delta"] = round(g["delta"], 4)
                side["call" if c.option_type == "call" else "put"] = quote
        finally:
            session.close()
        return {
            "underlying": u,
            "expiry": expiry.strip(),
            "underlying_close": u_close or None,
            "note": "价格为本地缓存收盘；无行情的合约缺 close 字段",
            "items": [rows[k] for k in sorted(rows)],
        }


# ----------------------------------------------------------------------
# 工具 schema（MCP 服务器 + Anthropic 工具的唯一真实来源）
# ----------------------------------------------------------------------

def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


_S = {"type": "string"}
_N = {"type": "number"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_D = {"type": "object"}

TOOL_SPECS: list[dict] = [
    {
        "name": "get_market_overview",
        "description": "获取市场行情总览：所有合约的最新价、涨跌幅、成交量。",
        "input_schema": _obj(
            {"gateway": {**_S, "description": "过滤网关名(SIM/STOCK/CTP)，空=全部"},
             "limit": {**_I, "description": "最多返回条数，默认30"}}),
        "readonly": True,
    },
    {
        "name": "get_tick",
        "description": "获取单个合约的最新行情快照（最新价/开高低/买卖一档/涨跌停）。",
        "input_schema": _obj(
            {"vt_symbol": {**_S, "description": "如 IF2509.CFFEX、600519.SSE"}},
            ["vt_symbol"]),
        "readonly": True,
    },
    {
        "name": "get_bars",
        "description": "获取历史K线（紧凑数组 [datetime,open,high,low,close,volume]）。数据库无数据时提示先下载。",
        "input_schema": _obj(
            {"vt_symbol": _S,
             "interval": {**_S, "enum": ["1m", "1h", "d", "w"], "description": "K线周期"},
             "limit": {**_I, "description": "返回最近N根，默认100"},
             "start": {**_S, "description": "起始时间 ISO 格式，可选"},
             "end": {**_S, "description": "结束时间，可选"}},
            ["vt_symbol"]),
        "readonly": True,
    },
    {
        "name": "get_key_levels",
        "description": "计算单个合约的9大类关键价位：筹码密集区/枢轴点/前高前低/布林带/Keltner/ATR通道/未回补缺口/斐波那契/整数关口。基于日K，含支撑/阻力/中枢标注与强度评分。",
        "input_schema": _obj(
            {"vt_symbol": {**_S, "description": "如 600519.SSE、IF2509.CFFEX"}},
            ["vt_symbol"]),
        "readonly": True,
    },
    {
        "name": "get_positions",
        "description": "获取当前所有持仓（合约/方向/数量/均价/盈亏）。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_accounts",
        "description": "获取账户资金（权益/可用/冻结）。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_orders",
        "description": "获取委托列表（最新在前）。",
        "input_schema": _obj(
            {"active_only": {**_B, "description": "只看活动委托"},
             "vt_symbol": {**_S, "description": "按合约过滤，可选"},
             "limit": {**_I, "description": "最多条数，默认20"}}),
        "readonly": True,
    },
    {
        "name": "get_trades",
        "description": "获取成交记录（最新在前，含逐笔平仓盈亏与手续费）。",
        "input_schema": _obj(
            {"vt_symbol": {**_S, "description": "按合约过滤，可选"},
             "limit": {**_I, "description": "最多条数，默认20"}}),
        "readonly": True,
    },
    {
        "name": "get_performance",
        "description": "最近N小时交易绩效摘要：总盈亏/胜率/手续费/未实现盈亏/涉及合约。",
        "input_schema": _obj(
            {"hours": {**_I, "description": "统计最近多少小时，默认24"}}),
        "readonly": True,
    },
    {
        "name": "get_intraday_bars",
        "description": "获取盘中实时K线（真实Tick合成，非历史库）。适合分析当天日内走势。",
        "input_schema": _obj(
            {"vt_symbol": _S,
             "interval": {**_S, "enum": ["1m", "5m", "15m", "30m", "1h", "d", "w"]},
             "limit": {**_I, "description": "最近N根，默认120"}},
            ["vt_symbol"]),
        "readonly": True,
    },
    {
        "name": "get_gateways",
        "description": "查看各网关（SIM/STOCK/CTP）连接状态与支持的交易所。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_logs",
        "description": "查看最近系统日志（连接/风控/策略/异常），可按关键字过滤。用于排查问题。",
        "input_schema": _obj(
            {"limit": {**_I, "description": "最多条数，默认30"},
             "keyword": {**_S, "description": "过滤关键字，可选"}}),
        "readonly": True,
    },
    {
        "name": "place_order",
        "description": "下单（受 AI_ALLOW_TRADING 配置与风控引擎约束）。direction: LONG=买/SHORT=卖；offset: OPEN=开仓/CLOSE=平仓。",
        "input_schema": _obj(
            {"vt_symbol": _S,
             "direction": {**_S, "enum": ["LONG", "SHORT"]},
             "volume": {**_N, "description": "数量(手)"},
             "price": {**_N, "description": "限价单价格；市价单填0"},
             "offset": {**_S, "enum": ["OPEN", "CLOSE", "CLOSETODAY", "CLOSEYESTERDAY"]},
             "order_type": {**_S, "enum": ["LIMIT", "MARKET"]}},
            ["vt_symbol", "direction", "volume"]),
        "readonly": False,
    },
    {
        "name": "cancel_order",
        "description": "撤销活动委托（受 AI_ALLOW_TRADING 约束）。",
        "input_schema": _obj({"vt_orderid": {**_S, "description": "如 SIM.3"}}, ["vt_orderid"]),
        "readonly": False,
    },
    {
        "name": "list_strategies",
        "description": "列出所有策略实例及其状态（inited/trading/pos/参数）。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "list_strategy_classes",
        "description": "列出可用策略类及其参数默认值。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "create_strategy",
        "description": "创建策略实例。",
        "input_schema": _obj(
            {"class_name": _S, "name": {**_S, "description": "实例名，唯一"},
             "vt_symbol": _S, "setting": {**_D, "description": "参数覆盖，可选"}},
            ["class_name", "name", "vt_symbol"]),
        "readonly": False,
    },
    {
        "name": "control_strategy",
        "description": "控制策略生命周期：init(初始化)/start(启动)/stop(停止)。",
        "input_schema": _obj(
            {"name": _S, "action": {**_S, "enum": ["init", "start", "stop"]}},
            ["name", "action"]),
        "readonly": False,
    },
    {
        "name": "edit_strategy",
        "description": "修改策略实例的参数（如均线周期、开仓手数）。建议先停止策略再修改。",
        "input_schema": _obj(
            {"name": {**_S, "description": "策略实例名"},
             "setting": {**_D, "description": "要修改的参数键值对"}},
            ["name", "setting"]),
        "readonly": False,
    },
    {
        "name": "list_strategy_files",
        "description": "列出用户策略目录下全部 .py 源码文件。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "read_strategy_file",
        "description": "读取某个策略文件的完整 Python 源码，用于解释或修改现有策略。",
        "input_schema": _obj(
            {"filename": {**_S, "description": "如 my_rsi_strategy.py"}},
            ["filename"]),
        "readonly": True,
    },
    {
        "name": "reconcile_positions",
        "description": "策略持仓对账：比较每个策略的 pos 与网关净持仓，找出不一致（如策略停止期间的手动交易）。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "sync_strategy_pos",
        "description": "把策略 pos 覆盖为网关净持仓（对账发现差异后使用；需策略已停止、合约未被多策略共用；受 AI_ALLOW_TRADING 约束）。",
        "input_schema": _obj(
            {"name": {**_S, "description": "策略实例名"}}, ["name"]),
        "readonly": False,
    },
    {
        "name": "write_strategy_file",
        "description": (
            "把策略 Python 源码写入用户策略目录并自动重载。代码必须定义 StrategyTemplate 子类，"
            "导入路径：from app.core.strategy.template import StrategyTemplate；"
            "可用 from app.core.strategy.array_manager import ArrayManager 等。"
            "禁止 import os/sys/subprocess 等系统模块。写入成功后可直接用 run_backtest 验证。"
        ),
        "input_schema": _obj(
            {"filename": {**_S, "description": "如 my_strategy.py（字母开头，不含路径）"},
             "code": {**_S, "description": "完整 Python 源码"},
             "overwrite": {**_B, "description": "覆盖已存在文件"}},
            ["filename", "code"]),
        "readonly": False,
    },
    {
        "name": "reload_strategies",
        "description": "重新扫描策略目录，刷新可用策略类列表。",
        "input_schema": _obj({}),
        "readonly": False,
    },
    {
        "name": "run_backtest",
        "description": "运行策略回测，返回统计指标（年化/回撤/夏普/胜率/盈亏比）与前10笔成交。需要数据库中已有对应区间的K线数据（无数据时先 download_backtest_data）。",
        "input_schema": _obj(
            {"class_name": _S, "vt_symbol": _S,
             "start": {**_S, "description": "回测开始时间，如 2026-07-02T09:00:00"},
             "end": {**_S, "description": "结束时间，可选"},
             "interval": {**_S, "enum": ["1m", "1h", "d"]},
             "capital": _N, "rate": {**_N, "description": "手续费率，默认0.0001"},
             "slippage": _N, "size": {**_N, "description": "合约乘数"},
             "pricetick": _N, "setting": {**_D, "description": "策略参数"}},
            ["class_name", "vt_symbol", "start"]),
        "readonly": False,
    },
    {
        "name": "download_backtest_data",
        "description": "从网关下载历史K线入库（SIM 网关生成确定性模拟数据，STOCK 网关拉真实A股日线）。",
        "input_schema": _obj(
            {"vt_symbol": _S,
             "start": {**_S, "description": "如 2026-07-01T09:00:00"},
             "end": _S,
             "interval": {**_S, "enum": ["1m", "1h", "d"]},
             "gateway_name": {**_S, "enum": ["SIM", "STOCK"], "description": "默认SIM"}},
            ["vt_symbol", "start"]),
        "readonly": False,
    },
    {
        "name": "get_risk_settings",
        "description": "查看风控参数与运行状态（限额/熔断标志/计数器）。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_strategy_health",
        "description": "查询策略守护档案：每个已建档策略的基线表现、最近复验结果与健康/观察/退役三态。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_market_regime",
        "description": "查询市场环境：自选池样本的4维加权评分(趋势/广度/动量/波动)与5档判定(强势/偏强/震荡/偏弱/弱势)，含近10日历史。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_market_phase",
        "description": "查询市场情绪：全市场涨停连板梯队统计(涨停家数/最高连板/晋级率/炸板率)与情绪周期6阶段判定(冰点/启动/主升/高潮/退潮/修复)，含连板梯队榜与近60日历史。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "run_screener",
        "description": "全市场策略扫描：6个内置信号策略(突破20日新高/放量上涨/均线多头/MA5金叉/涨停基因/RPS60≥90)扫描全部A股，返回每策略命中标的与信号详情。strategies留空=全部。",
        "input_schema": _obj(
            {"strategies": {"type": "array", "items": _S, "description": "策略id列表，可选"}},
            []),
        "readonly": True,
    },
    {
        "name": "get_abnormal_moves",
        "description": "异动监控预警：交易所异常波动口径（3日偏离主板±20%/创科±30%/北交所±40%）下的接近度排名，含触发/边缘/关注三级与10/30日涨幅参考。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_monitor_alerts",
        "description": "查询监控中心的告警记录（最新在前）与监控规则概览。",
        "input_schema": _obj(
            {"limit": {**_I, "description": "返回条数，默认20"}},
            []),
        "readonly": True,
    },
    {
        "name": "get_paper_overview",
        "description": "查询模拟盘账户总览：权益/现金/持仓/已实现与浮动盈亏/交易笔数（台账重放）。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "update_risk_settings",
        "description": "修改风控参数（如 order_size_limit/position_limit/daily_loss_limit_pct）。",
        "input_schema": _obj(
            {"updates": {**_D, "description": "要修改的参数键值对"}},
            ["updates"]),
        "readonly": False,
    },
    # ----- 同花顺金融数据（fuyao）特色数据源 -----
    {
        "name": "search_tickers",
        "description": "同花顺跨市场标的检索：按名称（如'贵州茅台'）、代码或thscode搜索，返回thscode/名称/交易所/资产类型。查询其它fuyao数据前可先用它消歧。",
        "input_schema": _obj(
            {"q": {**_S, "description": "关键词：thscode、代码或中英文名称"},
             "asset_type": {**_S, "enum": ["a-share", "a-share-index", "fund-etf", "fund-otc", "futures", "options"], "description": "资产类型过滤，可选"},
             "limit": {**_I, "description": "返回上限，默认10，最大50"}}),
        "readonly": True,
    },
    {
        "name": "get_capital_flow",
        "description": "同花顺主力资金实时快照：单只A股的超大单/大单/中单/小单流入流出净额（元）。",
        "input_schema": _obj(
            {"symbol": {**_S, "description": "A股标的，如 600519.SSE / 600519.SH / 600519"}},
            ["symbol"]),
        "readonly": True,
    },
    {
        "name": "get_capital_flow_history",
        "description": "同花顺主力资金历史序列：1d日级（默认近90天）或1m分钟级（默认近1天，最长7天）。",
        "input_schema": _obj(
            {"symbol": {**_S, "description": "A股标的"},
             "interval": {**_S, "enum": ["1d", "1m"], "description": "粒度，默认1d"},
             "start": {**_S, "description": "起始日期 2026-10-01，可选"},
             "end": {**_S, "description": "结束日期，可选"}}),
        "readonly": True,
    },
    {
        "name": "get_dragon_tiger",
        "description": "同花顺龙虎榜：默认最近交易日的全量榜单（净买入额排序前15，含涨停原因/机构净买/人气排名），hot_money 类型返回游资聚合榜。",
        "input_schema": _obj(
            {"date": {**_S, "description": "交易日 yyyy-MM-dd，默认最近交易日；仅支持一年内"},
             "board_type": {**_S, "enum": ["all", "org", "hot_money"], "description": "榜单类型，默认all"}}),
        "readonly": True,
    },
    {
        "name": "get_financials",
        "description": "同花顺三大财务报表：income利润表 / balance资产负债表 / cashflow现金流量表，annual年报或quarterly季报，金额单位元。",
        "input_schema": _obj(
            {"symbol": {**_S, "description": "A股标的"},
             "statement": {**_S, "enum": ["income", "balance", "cashflow"], "description": "报表类型，默认income"},
             "period": {**_S, "enum": ["annual", "quarterly"], "description": "报告期模式，默认annual"},
             "limit": {**_I, "description": "最近N期，默认4，最大20"}}),
        "readonly": True,
    },
    {
        "name": "get_financial_indicators",
        "description": "同花顺五类财务指标：成长/盈利/偿债/营运/现金流（如毛利率、ROE、资产负债率）。report 留空返回最近年报期。",
        "input_schema": _obj(
            {"symbol": {**_S, "description": "A股标的"},
             "report": {**_S, "description": "报告期 yyyy-1..4（如 2025-4 为2025年报），留空默认最近年报"}}),
        "readonly": True,
    },
    # ----- 同花顺金融数据（fuyao）：期货特色数据 -----
    {
        "name": "get_futures_main_contracts",
        "description": "同花顺期货主连与主力合约列表：各品种当前主力合约代码、名称、最后交易日、保证金率。回答『螺纹钢/沪铜主力是哪个合约』类问题。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "get_futures_warehouse_receipts",
        "description": "同花顺期货历史仓单：指定合约的仓单数量、增减量、折算手数序列（仓单下降常利多价格）。",
        "input_schema": _obj(
            {"symbol": {**_S, "description": "期货合约，如 rb2701.SHFE / RB2701.SHF / rb2701"},
             "start_date": {**_S, "description": "开始日期 yyyy-MM-dd"},
             "end_date": {**_S, "description": "结束日期 yyyy-MM-dd"}},
            ["symbol", "start_date", "end_date"]),
        "readonly": True,
    },
    {
        "name": "get_futures_basis",
        "description": "同花顺期货基差：留空返回全部品种主连最新基差（现货价/期价/基差/基差率，判断升贴水结构）；传合约返回历史基差序列。",
        "input_schema": _obj(
            {"symbol": {**_S, "description": "期货合约；留空=全部品种主连最新基差"}}),
        "readonly": True,
    },
    {
        "name": "get_futures_positions_variety",
        "description": "同花顺期货品种日持仓：全品种成交量/多单/空单/净持仓及变化、资金流向。date 留空=今天，非交易日请传最近交易日。",
        "input_schema": _obj(
            {"date": {**_S, "description": "交易日 yyyy-MM-dd，留空=今天"}}),
        "readonly": True,
    },
    # ----- 期权（fuyao 行情 + 本地合约目录） -----
    {
        "name": "get_options_varieties",
        "description": "期权品种资料：全部期权品种的交易所/合约乘数/手续费/交割方式（ETF期权、股指期权、商品期权）。回答『有哪些期权可交易』类问题。",
        "input_schema": _obj({}),
        "readonly": True,
    },
    {
        "name": "search_option_contracts",
        "description": "期权合约检索：按标的（如 510050/IO）可选类型与到期日过滤，返回行权价/最后交易日/合约单位。目录仅含在市合约。",
        "input_schema": _obj(
            {"underlying": {**_S, "description": "标的代码或股指品种，如 510050 / IO"},
             "option_type": {**_S, "enum": ["call", "put"], "description": "类型过滤，可选"},
             "expiry": {**_S, "description": "到期日 yyyy-MM-dd（最后交易日），可选"}},
            ["underlying"]),
        "readonly": True,
    },
    {
        "name": "get_option_daily",
        "description": "期权合约日K（OHLC+量额）。默认最近100根；给 start/end（yyyy-MM-dd）可取完整生命周期历史。",
        "input_schema": _obj(
            {"thscode": {**_S, "description": "期权合约 thscode，如 10011425.SH / IO2612-C-4000.CFE"},
             "start": {**_S, "description": "开始日期 yyyy-MM-dd，可选"},
             "end": {**_S, "description": "结束日期 yyyy-MM-dd，可选"}},
            ["thscode"]),
        "readonly": True,
    },
    {
        "name": "get_option_chain",
        "description": "期权链 T 型截面：某标的某到期日按行权价配对的认购/认沽最新收盘、隐含波动率、Delta（本地缓存，需先同步）。",
        "input_schema": _obj(
            {"underlying": {**_S, "description": "标的代码或股指品种，如 510050 / IO"},
             "expiry": {**_S, "description": "到期日 yyyy-MM-dd（最后交易日）"}},
            ["underlying", "expiry"]),
        "readonly": True,
    },
]


def execute_tool(tools: TradingTools, name: str, arguments: dict) -> str:
    """分发一次工具调用；错误会转为 LLM 可读的文本，绝不抛出异常。"""
    method = getattr(tools, name, None)
    spec_names = {s["name"] for s in TOOL_SPECS}
    if name not in spec_names or method is None:
        return f"错误: 未知工具 {name!r}"
    try:
        result = method(**arguments)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except ToolExecutionError as e:
        return f"错误: {e}"
    except TypeError as e:
        return f"错误: 参数不正确 - {e}"
    except httpx.HTTPError as e:
        return f"错误: 无法连接交易系统 API - {e}"
    except Exception as e:  # noqa: BLE001 — LLM 必须始终得到回复
        return f"错误: {type(e).__name__}: {e}"
