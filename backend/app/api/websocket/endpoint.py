"""WebSocket 端点，采用 JSON 订阅协议。

客户端 -> 服务端:
    {"action": "subscribe",   "channels": ["tick", ...], "symbols": ["IF2509.CFFEX"]}
    {"action": "unsubscribe", "channels": ["tick"], "symbols": [...]}
    {"action": "ping"}

服务端 -> 客户端:
    {"type": "connected" | "subscribed" | "pong" | "error", "data": {...}}
    {"type": "tick" | "order" | "trade" | "position" | "account" | "log"
            | "gateway_status", "data": {...}}
"""

import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException
from starlette import status

from ...auth import verify_token
from .manager import ConnectionManager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    # 认证：从 URL 查询参数获取 token
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        verify_token(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    manager: ConnectionManager = websocket.app.state.ws_manager
    await manager.connect(websocket)
    await websocket.send_json(
        {
            "type": "connected",
            "data": {"server_time": datetime.now().isoformat()},
        }
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "data": {"msg": "invalid JSON"}}
                )
                continue

            action = message.get("action")
            if action == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
            elif action in ("subscribe", "unsubscribe"):
                channels = message.get("channels", [])
                symbols = message.get("symbols")
                if not isinstance(channels, list):
                    await websocket.send_json(
                        {"type": "error", "data": {"msg": "channels must be a list"}}
                    )
                    continue
                sub = await manager.update_subscription(
                    websocket,
                    channels,
                    symbols,
                    subscribe=(action == "subscribe"),
                )
                if sub is not None:
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "data": {
                                "channels": sorted(sub.channels),
                                "symbols": (
                                    sorted(sub.symbols)
                                    if sub.symbols is not None
                                    else None
                                ),
                            },
                        }
                    )
            else:
                await websocket.send_json(
                    {"type": "error", "data": {"msg": f"unknown action: {action}"}}
                )
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)
