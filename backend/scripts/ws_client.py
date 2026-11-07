"""WebSocket 验证客户端。

用法（服务器必须已在运行）：
    python scripts/ws_client.py [vt_symbol]

订阅所有频道（tick 按给定合约代码过滤，
默认 IF2509.CFFEX）并打印收到的消息。
"""

import asyncio
import json
import sys

import websockets

WS_URL = "ws://127.0.0.1:8000/ws"


async def main() -> None:
    vt_symbol = sys.argv[1] if len(sys.argv) > 1 else "IF2509.CFFEX"

    async with websockets.connect(WS_URL) as ws:
        greeting = await ws.recv()
        print("<<", greeting)

        await ws.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "channels": [
                        "tick",
                        "order",
                        "trade",
                        "position",
                        "account",
                        "log",
                        "gateway_status",
                    ],
                    "symbols": [vt_symbol],
                }
            )
        )

        while True:
            raw = await ws.recv()
            message = json.loads(raw)
            msg_type = message.get("type")
            data = message.get("data", {})
            if msg_type == "tick":
                print(
                    f"[tick] {data['vt_symbol']} last={data['last_price']} "
                    f"bid1={data['bid_price_1']} ask1={data['ask_price_1']} "
                    f"vol={data['volume']} @ {data['datetime']}"
                )
            else:
                print(f"[{msg_type}] {json.dumps(data, ensure_ascii=False)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
