"""关注标的的一键 AI 分析：素材组装 + 固定模板 + 结果落盘。

素材与关注页详情同源（watchlist_view.detail + 近30日收盘 +
期权链摘要），工具白名单精简（避免全量工具 schema 膨胀触发
tpm/rpm 限流，与盘后复盘同一策略）。
"""

from __future__ import annotations

import json
from datetime import datetime

from ..config import DATA_DIR
from ..core.utility import CHINA_TZ

ANALYSES_FILE = DATA_DIR / "ai_analyses.json"
MAX_SAVED = 20

ANALYZE_PROMPT = """你是一名专业的量化交易分析助手。请基于下面给出的标的素材数据，
输出该标的的分析报告（Markdown），结构如下：

## 结论（一句话）
## 行情与位置（最新价、近期涨跌、所处位置）
## 关键价位（上方压力/下方支撑，给出具体价位与操作含义）
## 异动与信号（近期异动行、监控告警；期权标的给出 IV 结构含义）
## 期权视角（仅期权类标的：IV 水平/期限结构/行权价位置；无则写"不适用"）
## 风险提示
## 操作参考（区间思路，不构成投资建议）

要求：数据引用具体数字；结论明确；不超过 600 字。

--- 标的素材 ---

{material}"""

# 工具白名单：单标的分析只需行情/价位/期权查询子集
ANALYZE_TOOLS = [
    "get_tick",
    "get_bars",
    "get_key_levels",
    "get_intraday_bars",
    "get_market_regime",
    "get_option_chain",
    "get_option_daily",
    "search_option_contracts",
]


def build_material(main_engine, item_id: str, kind: str) -> str:  # noqa: ANN001
    """把单标的素材拼成模型可读文本（与关注页详情同源）。"""
    from .watchlist_view import detail

    d = detail(main_engine, item_id, kind)
    lines: list[str] = [
        f"标的: {d.get('name')} ({item_id})",
        f"类型: {kind}",
    ]
    q = d.get("quote") or {}
    if q.get("last") is not None:
        src_label = "实时" if q.get("source") == "tick" else "日K收盘"
        lines.append(
            f"最新价: {q['last']} ({src_label}"
            + (f", {q.get('as_of')}" if q.get("as_of") else "")
            + (f", 涨跌 {q['change_pct'] * 100:+.2f}%" if q.get("change_pct") is not None else "")
            + ")"
        )
    else:
        lines.append("最新价: 暂无行情数据（未订阅或无本地日K）")

    kl = d.get("key_levels")
    if isinstance(kl, dict) and kl.get("levels"):
        lines.append("关键价位（前10）:")
        for lv in kl["levels"][:10]:
            lines.append(
                f"  {lv.get('type')} {lv.get('label')} @ {lv.get('value')} "
                f"({lv.get('side')}, 距现价 {lv.get('distance_pct')}%, 强度 {lv.get('strength')})"
            )
    elif isinstance(kl, dict) and kl.get("error"):
        lines.append(f"关键价位: 计算失败（{kl['error']}）")

    ab = d.get("abnormal")
    if ab:
        lines.append(
            f"全市场异动: {ab.get('level')} (接近度 {ab.get('closeness')}, 3日 {ab.get('ret3_pct')}%)"
        )
    alerts = d.get("alerts") or []
    if alerts:
        lines.append("近期监控告警:")
        for a in alerts[:5]:
            lines.append(f"  [{a.get('time', '')[:16]}] {a.get('message')}")

    contract = d.get("contract")
    if contract:
        lines.append(
            f"期权合约: {contract.get('option_type')} 行权价 {contract.get('strike_price')} "
            f"到期 {contract.get('last_trade_date')} 合约单位 {contract.get('trade_amount')}"
        )
    iv = d.get("iv_snapshot")
    if iv:
        lines.append(
            f"期权估值: 收盘 {iv.get('price')} ({iv.get('as_of')}) "
            f"IV {iv.get('iv')} Delta {iv.get('delta')} "
            f"剩余 {iv.get('days_to_expiry')} 天"
            + (f" 标的收盘 {iv.get('underlying_close')}" if iv.get("underlying_close") else "")
        )
    iv_term = d.get("iv_term") or []
    if iv_term:
        rows = [
            f"{it['expiry']}: C-IV {it.get('call_iv')} / P-IV {it.get('put_iv')}"
            for it in iv_term
        ]
        lines.append("ATM 隐波期限结构:\n  " + "\n  ".join(rows))

    closes = d.get("closes") or []
    if closes:
        tail = closes[-10:]
        lines.append(
            "近10日收盘: "
            + ", ".join(f"{c['date'][5:]}={c['close']}" for c in tail if c.get("close"))
        )
    return "\n".join(lines)


def save_analysis(item_id: str, kind: str, reply: str, tool_calls: int) -> None:
    entries: list[dict] = []
    try:
        if ANALYSES_FILE.is_file():
            entries = json.loads(ANALYSES_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        entries = []
    entries.insert(
        0,
        {
            "id": item_id,
            "kind": kind,
            "time": datetime.now(CHINA_TZ).isoformat(),
            "reply": reply,
            "tool_calls": tool_calls,
        },
    )
    ANALYSES_FILE.parent.mkdir(parents=True, exist_ok=True)
    ANALYSES_FILE.write_text(
        json.dumps(entries[:MAX_SAVED], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_analyses(item_id: str | None = None, limit: int = 20) -> list[dict]:
    try:
        if ANALYSES_FILE.is_file():
            entries = json.loads(ANALYSES_FILE.read_text(encoding="utf-8"))
            if item_id:
                entries = [e for e in entries if e.get("id") == item_id]
            return entries[:limit]
    except Exception:  # noqa: BLE001
        pass
    return []
