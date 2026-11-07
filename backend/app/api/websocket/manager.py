"""WebSocket 连接管理器，带有针对每个客户端的订阅过滤。"""

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket

VALID_CHANNELS = {
    "tick",
    "order",
    "trade",
    "position",
    "account",
    "log",
    "gateway_status",
    "strategy",
    "strategy_log",
}


@dataclass
class ClientSubscription:
    """已连接客户端希望接收的内容。"""

    channels: set[str] = field(default_factory=set)
    # None = 所有合约；仅应用于 tick 频道
    symbols: set[str] | None = None


class ConnectionManager:
    """跟踪已连接的 WebSocket 客户端并广播消息。"""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, ClientSubscription] = {}
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = ClientSubscription()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)

    async def update_subscription(
        self,
        websocket: WebSocket,
        channels: list[str],
        symbols: list[str] | None,
        subscribe: bool = True,
    ) -> ClientSubscription | None:
        """为单个客户端添加或移除频道/合约。"""
        async with self._lock:
            sub = self._clients.get(websocket)
            if sub is None:
                return None
            valid = {c for c in channels if c in VALID_CHANNELS}
            if subscribe:
                sub.channels |= valid
                if symbols is not None:
                    if sub.symbols is None:
                        sub.symbols = set()
                    sub.symbols |= set(symbols)
            else:
                sub.channels -= valid
                if symbols:
                    if sub.symbols is not None:
                        sub.symbols -= set(symbols)
                        if not sub.symbols:
                            sub.symbols = None
            return sub

    async def broadcast(self, message: dict) -> None:
        """向所有订阅匹配的客户端发送消息。

        采用并发发送，这样某个慢速客户端不会阻塞
        其他客户端接收 tick。
        """
        msg_type: str = message.get("type", "")
        vt_symbol: str | None = None
        if msg_type == "tick":
            vt_symbol = message.get("data", {}).get("vt_symbol")

        async with self._lock:
            targets = [
                ws
                for ws, sub in self._clients.items()
                if msg_type in sub.channels
                and (
                    msg_type != "tick"
                    or sub.symbols is None
                    or vt_symbol in sub.symbols
                )
            ]

        if not targets:
            return

        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        dead: list[WebSocket] = []
        await asyncio.gather(*(_send(ws) for ws in targets))
        for ws in dead:
            await self.disconnect(ws)
