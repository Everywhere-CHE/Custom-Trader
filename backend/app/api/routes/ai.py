"""带对话管理的 AI 助手端点。"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ...ai.engine import AIEngine
from ...core.engine.main_engine import MainEngine
from ...ai.presets import PRESETS
from ...conversations import _extract_display, get_conversation, update_conversation
from ..schemas import AiConfigBody, ChatRequestBody

router = APIRouter(prefix="/ai", tags=["ai"])


def get_ai_engine(request: Request) -> AIEngine:
    engine = getattr(request.app.state, "ai_engine", None)
    if engine is None:
        raise HTTPException(503, "AI engine not initialized")
    return engine


@router.get("/status")
def ai_status(request: Request) -> dict:
    return get_ai_engine(request).status()


@router.get("/presets")
def ai_presets() -> list[dict]:
    """供前端快速切换 UI 使用的主流模型预设。"""
    return PRESETS


@router.put("/config")
def ai_update_config(body: AiConfigBody, request: Request) -> dict:
    """在运行时更新 provider/model/base_url/api_key/allow_trading/auto_optimize。"""
    engine = get_ai_engine(request)
    try:
        return engine.update_config(
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
            allow_trading=body.allow_trading,
            auto_optimize=body.auto_optimize,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.put("/auto-optimize")
def ai_toggle_auto_optimize(body: dict, request: Request) -> dict:
    """开关自动策略优化功能。"""
    enabled = body.get("enabled", False)
    engine = get_ai_engine(request)
    return engine.update_config(auto_optimize=enabled)


@router.post("/chat")
async def ai_chat(body: ChatRequestBody, request: Request) -> dict:
    engine = get_ai_engine(request)
    if not engine.enabled:
        raise HTTPException(
            503,
            "No API Key configured. Please set it in the AI settings page.",
        )

    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    if not messages:
        raise HTTPException(422, "messages cannot be empty")

    try:
        result = await run_in_threadpool(engine.chat, messages)
    except Exception as e:
        raise HTTPException(502, f"Model call failed: {e}") from None

    # 保存/更新对话：从完整对话中提取用于展示的消息，
    # 以便把 AI 的回复也包含进来。
    conv_id = body.conversation_id
    if conv_id:
        conv = get_conversation(conv_id)
        if conv:
            update_conversation(
                conv_id, result.messages,
                display_messages=_extract_display(result.messages),
            )

    return {
        "reply": result.reply,
        "tool_calls": [asdict(t) for t in result.tool_calls],
        "messages": result.messages,
    }

# ----------------------------------------------------------------------
# 关注标的一键 AI 分析
# ----------------------------------------------------------------------

@router.post("/analyze")
async def ai_analyze(body: dict, request: Request) -> dict:
    """单标的 AI 分析：素材与关注页详情同源，结果落盘
    data/ai_analyses.json（最近 20 条）。body: {id, kind?}。
    """
    from ...services.watchlist_analysis import (
        ANALYZE_PROMPT,
        ANALYZE_TOOLS,
        build_material,
        load_analyses,
        save_analysis,
    )
    from ...watchlist import _infer_kind

    item_id = str(body.get("id") or body.get("vt_symbol") or "").strip()
    if not item_id:
        raise HTTPException(422, "id 不能为空")
    kind = str(body.get("kind") or "").strip() or _infer_kind(item_id)

    engine = get_ai_engine(request)
    if not engine.enabled:
        raise HTTPException(
            503,
            "No API Key configured. Please set it in the AI settings page.",
        )
    main_engine: MainEngine = request.app.state.main_engine
    material = build_material(main_engine, item_id, kind)
    messages = [
        {
            "role": "user",
            "content": f"{ANALYZE_PROMPT.replace('{material}', material)}",
        }
    ]
    try:
        result = await run_in_threadpool(engine.chat, messages, ANALYZE_TOOLS)
    except Exception as e:
        raise HTTPException(502, f"Model call failed: {e}") from None

    save_analysis(item_id, kind, result.reply, len(result.tool_calls))
    return {
        "id": item_id,
        "kind": kind,
        "analysis": result.reply,
        "tool_calls": [asdict(t) for t in result.tool_calls],
    }


@router.get("/analyze/history")
def ai_analyze_history(id: str | None = None, limit: int = 20) -> dict:
    from ...services.watchlist_analysis import load_analyses

    return {"items": load_analyses(id, limit)}


# ----------------------------------------------------------------------
# 盘后 AI 复盘
# ----------------------------------------------------------------------

@router.post("/recap")
async def ai_recap(request: Request) -> dict:
    """生成盘后复盘：本地市场快照 + 固定 8 节模板，AI 解读。

    快照与页面同源（OMS/日K）；AI 可经工具循环补充守护状态、
    关键价位等。报告落盘 data/ai_recaps.json（最近 20 条）。
    """
    from ...services.market_recap import (
        RECAP_PROMPT,
        build_snapshot,
        save_recap,
        snapshot_to_text,
    )

    engine = get_ai_engine(request)
    if not engine.enabled:
        raise HTTPException(
            503,
            "No API Key configured. Please set it in the AI settings page.",
        )
    main_engine: MainEngine = request.app.state.main_engine

    snapshot = build_snapshot(main_engine)
    from ...services.market_recap import build_market_research_text

    material = snapshot_to_text(snapshot) + build_market_research_text()
    messages = [
        {
            "role": "user",
            "content": f"{RECAP_PROMPT}\n\n---\n\n{material}",
        }
    ]

    # 复盘只带精简工具子集：全量 43 个工具的 schema 会让每轮请求
    # 膨胀到 ~2 万 token，多轮循环极易触发代理的 tpm/rpm 限流
    recap_tools = [
        "get_market_overview",
        "get_tick",
        "get_bars",
        "get_key_levels",
        "get_market_regime",
        "get_strategy_health",
        "get_positions",
        "get_monitor_alerts",
    ]

    try:
        result = await run_in_threadpool(
            engine.chat, messages, recap_tools
        )
    except Exception as e:
        raise HTTPException(502, f"Model call failed: {e}") from None

    save_recap(result.reply, {"time": snapshot["time"], "subscribed": snapshot["subscribed_count"]})
    return {
        "recap": result.reply,
        "snapshot_time": snapshot["time"],
        "subscribed_count": snapshot["subscribed_count"],
        "tool_calls": [asdict(t) for t in result.tool_calls],
    }


@router.get("/recap/history")
def ai_recap_history(limit: int = 10) -> dict:
    """历史复盘报告（最近 N 条，默认 10）。"""
    from ...services.market_recap import load_recaps

    return {"recaps": load_recaps(min(max(limit, 1), 20))}
