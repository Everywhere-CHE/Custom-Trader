"""盘后 AI 复盘：把本地市场快照拼装成结构化素材，交给 AI 按固定
模板生成复盘报告。

设计要点：
- 数据与页面**同源**（全部取自 OMS 快照与本地日 K），不另起口径；
- 快照在服务端拼好，AI 只负责解读（也可经工具循环补充
  守护状态/关键价位等）；
- 固定 8 节 Markdown 模板 + 强约束"不输出买卖指令"；
- 报告持久化 ``data/ai_recaps.json``（最近 20 条）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR
from ..core.constant import Direction
from ..core.utility import CHINA_TZ

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine

RECAPS_FILE = DATA_DIR / "ai_recaps.json"
MAX_SAVED = 20

RECAP_PROMPT = """你是一名专业的量化交易复盘助手。请基于下面给出的市场快照数据，
按固定 8 节结构输出今日盘后复盘（Markdown）：

## 1. 一句话定调
## 2. 盘面总览
## 3. 指数与品种结构
## 4. 主力品种与异动
## 5. 持仓与账户
## 6. 资金与情绪观察
## 7. 后续观察要点
## 8. 风险提示

要求：
- 只使用快照与工具返回的真实数据，没有的数据明确写"暂无数据"，禁止编造；
- 素材中的「全市场研究数据」段（情绪指数/连板梯队/异动名单/扫描结果）必须
  在第 6 节「资金与情绪观察」和第 4 节「主力品种与异动」中引用，不得忽略；
  异动触发标的需按"涨/跌"分别点名；
- "后续观察要点"给出具体的价格位/条件（可引用关键价位工具的输出）；
- 全文不得出现任何买卖指令或操作建议，只做客观事实与风险的陈述；
- 结尾固定附一行："> 以上内容由 AI 基于本地数据生成，仅供研究参考，不构成投资建议。"
"""


def build_snapshot(main_engine: "MainEngine") -> dict:
    """从 OMS / 数据引擎拼装复盘素材（与页面同源）。"""
    from ..core.constant import Exchange, Interval

    oms = main_engine.oms
    data_engine = main_engine.get_engine("data")

    # 网关状态
    gateways = [
        {"name": gw_name, "connected": bool(getattr(gw, "connected", False))}
        for gw_name, gw in main_engine.gateways.items()
    ]

    # 实时快照 → 涨跌榜
    tickers: list[dict] = []
    for tick in oms.get_all_ticks():
        if not tick.last_price or not tick.pre_close:
            continue
        pct = (tick.last_price - tick.pre_close) / tick.pre_close * 100
        tickers.append(
            {
                "vt_symbol": tick.vt_symbol,
                "name": tick.name or "",
                "last": tick.last_price,
                "pre_close": tick.pre_close,
                "pct_chg": round(pct, 2),
                "volume": tick.volume or 0,
                "high": tick.high_price,
                "low": tick.low_price,
                "open": tick.open_price,
            }
        )
    tickers.sort(key=lambda t: t["pct_chg"], reverse=True)
    gainers = tickers[:5]
    losers = list(reversed(tickers[-5:])) if len(tickers) > 5 else []
    # 平紧要品种（成交额榜）：近似 amount = last * volume
    by_amount = sorted(
        tickers, key=lambda t: (t["last"] or 0) * (t["volume"] or 0), reverse=True
    )[:8]

    # 持仓
    positions = []
    for pos in oms.get_all_positions():
        if not pos.volume:
            continue
        positions.append(
            {
                "vt_symbol": pos.vt_symbol,
                "direction": "多" if pos.direction == Direction.LONG else "空",
                "volume": pos.volume,
                "price": pos.price,
                "pnl": round(pos.pnl or 0.0, 2),
            }
        )

    # 账户
    accounts = [
        {
            "accountid": a.vt_accountid,
            "balance": round(a.balance or 0.0, 2),
            "available": round(a.available or 0.0, 2),
        }
        for a in oms.get_all_accounts()
    ]

    # 订阅品种的近 5 日收盘走势（本地日 K）
    daily_series: dict[str, list] = {}
    if data_engine is not None:
        seen: set[str] = set()
        for t in tickers:
            vt_symbol = t["vt_symbol"]
            if vt_symbol in seen:
                continue
            seen.add(vt_symbol)
            try:
                symbol, exchange = vt_symbol.rsplit(".", 1)
                bars = data_engine.load_bars_tail(
                    symbol, Exchange(exchange), Interval.DAILY, 6
                )
                if len(bars) >= 2:
                    daily_series[vt_symbol] = [
                        {
                            "date": b.datetime.strftime("%m-%d"),
                            "close": b.close_price,
                            "volume": b.volume,
                        }
                        for b in bars[-6:]
                    ]
            except Exception:  # noqa: BLE001 —— 单品种失败不影响整体
                continue

    now = datetime.now(CHINA_TZ)
    return {
        "time": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": "一二三四五六日"[now.weekday()],
        "gateways": gateways,
        "subscribed_count": len(tickers),
        "gainers": gainers,
        "losers": losers,
        "active_by_amount": by_amount,
        "positions": positions,
        "accounts": accounts,
        "daily_series": daily_series,
    }


def snapshot_to_text(snapshot: dict) -> str:
    """快照 → 紧凑 Markdown 素材。"""
    lines: list[str] = []
    lines.append(f"# 市场快照（{snapshot['time']} 周{snapshot['weekday']}）")
    lines.append(
        "网关："
        + "、".join(
            f"{g['name']}({'已连接' if g['connected'] else '未连接'})"
            for g in snapshot["gateways"]
        )
        + f"；订阅品种数 {snapshot['subscribed_count']}"
    )

    def fmt_table(title: str, rows: list[dict]) -> None:
        lines.append(f"\n## {title}")
        if not rows:
            lines.append("（无数据）")
            return
        lines.append("| 品种 | 名称 | 最新 | 涨跌% | 成交量 |")
        lines.append("|---|---|---|---|---|")
        for r in rows:
            lines.append(
                f"| {r['vt_symbol']} | {r['name']} | {r['last']} | {r['pct_chg']:+.2f} | {r['volume']} |"
            )

    fmt_table("涨幅榜", snapshot["gainers"])
    fmt_table("跌幅榜", snapshot["losers"])
    fmt_table("活跃品种（按成交额）", snapshot["active_by_amount"])

    lines.append("\n## 持仓")
    if snapshot["positions"]:
        for p in snapshot["positions"]:
            lines.append(
                f"- {p['vt_symbol']} {p['direction']} {p['volume']} 手/股，均价 {p['price']}，浮盈亏 {p['pnl']}"
            )
    else:
        lines.append("（无持仓）")

    lines.append("\n## 账户")
    for a in snapshot["accounts"]:
        lines.append(
            f"- {a['accountid']}：权益 {a['balance']}，可用 {a['available']}"
        )

    lines.append("\n## 近 5 日收盘走势")
    for vt_symbol, series in list(snapshot["daily_series"].items())[:12]:
        closes = " → ".join(f"{d['date']}:{d['close']}" for d in series[-5:])
        lines.append(f"- {vt_symbol}: {closes}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 报告持久化
# ----------------------------------------------------------------------

def save_recap(recap: str, snapshot_meta: dict) -> None:
    entries: list[dict] = []
    try:
        if RECAPS_FILE.is_file():
            entries = json.loads(RECAPS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        entries = []
    entries.append(
        {"time": datetime.now(CHINA_TZ).isoformat(), "meta": snapshot_meta, "recap": recap}
    )
    entries = entries[-MAX_SAVED:]
    RECAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECAPS_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_recaps(limit: int = 20) -> list[dict]:
    try:
        if RECAPS_FILE.is_file():
            entries = json.loads(RECAPS_FILE.read_text(encoding="utf-8"))
            return entries[-limit:]
    except Exception:  # noqa: BLE001
        pass
    return []


def build_market_research_text() -> str:
    """拼装全市场研究数据段（情绪/异动/扫描），供复盘素材与 AI 工具复用。

    每块独立 try/except：单块失败不影响其余，fail-closed 降级。
    """
    lines = ["\n# 全市场研究数据（本地自动计算）"]

    # 1) 情绪 6 阶段 + 连板梯队
    try:
        from .market_phase import get_market_phase_cached

        ph = get_market_phase_cached()
        if ph.get("status") == "ok":
            s = ph.get("sentiment_now") or {}
            ls = ph.get("ladder_stats") or {}
            lines.append(
                f"- 情绪指数 {s.get('score')}（{s.get('zone')}），"
                f"周期阶段 {ph.get('phase')}（截至 {ph.get('as_of')}）"
            )
            lines.append(
                f"- 涨停 {ls.get('limit_up')} / 首板 {ls.get('first_board')} / "
                f"2板+ {ls.get('ge2')} / 3板+ {ls.get('ge3')} / 5板+ {ls.get('ge5')} / "
                f"最高 {ls.get('max_streak')} 板 / 跌停 {ls.get('limit_down')}"
            )
            pr, bl = ls.get("promotion_rate"), ls.get("blast_rate")
            lines.append(
                f"- 晋级率 {pr if pr is not None else '样本不足'} / "
                f"炸板率 {bl if bl is not None else '-'}"
            )
            top = ph.get("ladder_top", [])[:8]
            if top:
                lines.append(
                    "- 连板梯队："
                    + "、".join(f"{it['streak']}板 {it['name']}" for it in top)
                )
    except Exception:  # noqa: BLE001
        lines.append("- 情绪数据不可用")

    # 2) 异动监控（交易所口径）
    try:
        from .abnormal_moves import compute_abnormal

        ab = compute_abnormal()
        if ab.get("status") == "ok":
            c = ab.get("counts") or {}
            lines.append(
                f"- 异动（3日偏离口径）：触发 {c.get('触发', 0)} / "
                f"边缘 {c.get('边缘', 0)} / 关注 {c.get('关注', 0)}"
                f"（全市场3日均 {ab.get('baseline3_pct')}%）"
            )
            ups = [it for it in ab.get("items", []) if "涨" in it["level"]][:8]
            downs = [it for it in ab.get("items", []) if "跌" in it["level"]][:8]
            if ups:
                lines.append(
                    "- 向上触发："
                    + "、".join(f"{it['name']}({it['ret3_pct']:+.1f}%)" for it in ups)
                )
            if downs:
                lines.append(
                    "- 向下触发："
                    + "、".join(f"{it['name']}({it['ret3_pct']:+.1f}%)" for it in downs)
                )
    except Exception:  # noqa: BLE001
        lines.append("- 异动数据不可用")

    # 3) 全市场扫描
    try:
        from .screener import get_scan_cached

        sc = get_scan_cached([])
        if sc.get("status") == "ok":
            for s in sc.get("strategies", []):
                ex = "、".join(it["name"] for it in s.get("items", [])[:8])
                lines.append(
                    f"- 扫描[{s['name']}]：{s['count']} 只"
                    + (f"（{ex}）" if ex else "")
                )
    except Exception:  # noqa: BLE001
        lines.append("- 扫描数据不可用")

    return "\n".join(lines)
