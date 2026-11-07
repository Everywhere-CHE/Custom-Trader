"""CTP / SimNow 连通性测试（独立运行，无需 Web 服务器）。

从 backend/.env 读取 CTP 配置，连接交易前置和行情前置，打印
合约 / 账户 / 持仓，可选地订阅一个合约，然后退出。请在期货
交易时段运行（或连接 SimNow 7x24 测试环境）。

用法：
    python scripts/ctp_connect_test.py [symbol_to_subscribe]

运行前：在 .env 中填写 CTP_PASSWORD（如有需要还包括地址）。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings                       # noqa: E402
from app.core.constant import Exchange                    # noqa: E402
from app.core.event import (                              # noqa: E402
    EVENT_ACCOUNT,
    EVENT_CONTRACT,
    EVENT_LOG,
    EVENT_POSITION,
    EVENT_TICK,
    EventEngine,
)
from app.core.gateway.ctp import CtpGateway               # noqa: E402
from app.core.object import SubscribeRequest              # noqa: E402


def main() -> None:
    settings = get_settings()
    setting = settings.ctp_setting()

    if setting["password"] in ("", "changeme"):
        print("ERROR: fill CTP_PASSWORD in backend/.env first")
        sys.exit(1)

    print(f"Connecting CTP: broker={setting['brokerid']} "
          f"user={setting['userid']}")
    print(f"  td: {setting['td_address']}")
    print(f"  md: {setting['md_address']}")

    engine = EventEngine()
    contracts: list = []
    engine.register(EVENT_LOG, lambda e: print(f"  [log] {e.data.msg}"))
    engine.register(EVENT_CONTRACT, lambda e: contracts.append(e.data))
    engine.register(
        EVENT_ACCOUNT,
        lambda e: print(
            f"  [account] {e.data.accountid} balance={e.data.balance:.2f} "
            f"available={e.data.available:.2f}"
        ),
    )
    engine.register(
        EVENT_POSITION,
        lambda e: print(
            f"  [position] {e.data.vt_symbol} {e.data.direction.value} "
            f"vol={e.data.volume} yd={e.data.yd_volume} price={e.data.price:.2f}"
        ),
    )
    engine.register(
        EVENT_TICK,
        lambda e: print(
            f"  [tick] {e.data.vt_symbol} last={e.data.last_price} "
            f"bid1={e.data.bid_price_1} ask1={e.data.ask_price_1} "
            f"@ {e.data.datetime}"
        ),
    )
    engine.start()

    gateway = CtpGateway(engine, "CTP")
    gateway.connect(setting)

    print("Waiting up to 30s for login + contract download ...")
    for _ in range(30):
        time.sleep(1)
        if gateway.connected:
            break

    if not gateway.connected:
        print("FAILED: gateway did not become connected within 30s "
              "(check password / trading hours / addresses)")
        gateway.close()
        engine.stop()
        sys.exit(2)

    print(f"CONNECTED. Contracts received: {len(contracts)}")
    sample = [c.vt_symbol for c in contracts[:10]]
    print(f"  sample: {sample}")

    if len(sys.argv) > 1:
        symbol = sys.argv[1]
        contract = next((c for c in contracts if c.symbol == symbol), None)
        if contract:
            print(f"Subscribing {contract.vt_symbol} for 20s ...")
            gateway.subscribe(
                SubscribeRequest(symbol=symbol, exchange=contract.exchange)
            )
            time.sleep(20)
        else:
            print(f"Symbol {symbol} not found in contracts")
    else:
        # 轮询几秒钟以展示账户/持仓信息
        time.sleep(8)

    gateway.close()
    engine.stop()
    print("Done.")


if __name__ == "__main__":
    main()
