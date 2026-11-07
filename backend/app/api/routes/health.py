"""健康检查端点。

返回系统真实运行状态：引擎、网关、WebSocket、事件队列等。
当系统无法正常提供交易/行情服务时（如所有真实网关断连、
引擎停止），返回 503 并给出原因，供外部监控或手动排查判断。
"""

from fastapi import APIRouter, Depends, Request

from ...config import get_settings
from ...core.constant import EngineState
from ...core.engine.main_engine import MainEngine
from ..deps import get_main_engine

router = APIRouter(tags=["system"])


@router.get("/health")
def health(
    request: Request,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    settings = get_settings()
    ws_manager = getattr(request.app.state, "ws_manager", None)
    bridge = getattr(request.app.state, "event_bridge", None)

    engine_state = main_engine.state.value
    gateways = {
        name: gateway.connected
        for name, gateway in main_engine.gateways.items()
    }
    ws_clients = ws_manager.client_count if ws_manager else 0
    event_queue_size = main_engine.event_engine.queue_size
    dropped_ws_msgs = bridge.dropped_count if bridge else 0

    # ---- 评估真实健康状态 ----
    # 真实网关（排除 SIM 模拟）中，至少有一个已连接才算可用
    real_gateways = {
        name: connected
        for name, connected in gateways.items()
        if name != "SIM"
    }
    any_gateway_up = (
        bool(real_gateways) and any(real_gateways.values())
    )
    engine_running = main_engine.state == EngineState.RUNNING
    # 事件队列严重积压（>5000）视为异常
    queue_backed_up = event_queue_size > 5000

    problems: list[str] = []
    if not engine_running:
        problems.append(f"engine_state={engine_state}")
    if real_gateways and not any_gateway_up:
        problems.append("no real gateway connected")
    if queue_backed_up:
        problems.append(f"event_queue_backed_up={event_queue_size}")

    healthy = (engine_running and any_gateway_up and not queue_backed_up)

    body = {
        "status": "ok" if healthy else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "engine_state": engine_state,
        "gateways": gateways,
        "ws_clients": ws_clients,
        "event_queue_size": event_queue_size,
        "event_stats": main_engine.event_engine.stats,
        "dropped_ws_msgs": dropped_ws_msgs,
    }
    if problems:
        body["problems"] = problems

    if not healthy:
        from fastapi.exceptions import HTTPException
        raise HTTPException(status_code=503, detail=body)

    return body