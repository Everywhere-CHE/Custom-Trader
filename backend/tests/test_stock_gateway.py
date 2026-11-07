"""StockGateway 测试（腾讯行情解析；网络部分可选）。"""

import time

import pytest

from app.core.constant import Direction, Exchange, Interval, OrderType
from app.core.event import EVENT_CONTRACT, EVENT_TICK, EventEngine
from app.core.gateway.stock import StockGateway
from app.core.gateway.stock.stock_gateway import to_tencent_code
from app.core.object import HistoryRequest, OrderRequest, SubscribeRequest
from datetime import datetime


# sh600519 的真实腾讯行情抓包数据（2026-07-17）
SAMPLE_PAYLOAD = (
    "1~贵州茅台~600519~1253.00~1258.99~1269.01~58417~32550~25868~1252.99~36~"
    "1252.97~1~1252.96~1~1252.90~1~1252.70~1~1253.00~20~1253.32~1~1253.75~1~"
    "1254.00~2~1254.40~1~~20260717161459~-5.99~-0.48~1269.33~1238.98~"
    "1253.00/58417/7322732709~58417~732273~0.47~18.94~~1269.33~1238.98~2.41~"
    "15663.52~15663.52~13.87~1384.89~1133.09~1.35~0~0~0~0~0~0~0~0~0~0~0~0~0~"
    "0~0~0~GP-A~0~0~0~0~0~0~0~0~0~0~0~0~0~0"
)


def test_to_tencent_code():
    assert to_tencent_code("600519", Exchange.SSE) == "sh600519"
    assert to_tencent_code("000001", Exchange.SZSE) == "sz000001"


def test_parse_quote_fields():
    engine = EventEngine(interval=10)
    gateway = StockGateway(engine, "STOCK")
    gateway._register_contract("600519", Exchange.SSE, "贵州茅台")
    contract = gateway._contracts["600519.SSE"]

    fields = SAMPLE_PAYLOAD.split("~")
    tick = gateway._parse_quote(fields, contract)

    assert tick is not None
    assert tick.last_price == 1253.00
    assert tick.pre_close == 1258.99
    assert tick.open_price == 1269.01
    assert tick.volume == 58417 * 100
    assert tick.high_price == 1269.33
    assert tick.low_price == 1238.98
    assert tick.limit_up == 1384.89
    assert tick.limit_down == 1133.09
    # 五档盘口
    assert tick.bid_price_1 == 1252.99
    assert tick.bid_volume_1 == 36 * 100
    assert tick.ask_price_1 == 1253.00
    assert tick.ask_volume_1 == 20 * 100
    assert tick.bid_price_5 == 1252.70
    assert tick.ask_price_5 == 1254.40
    assert tick.datetime == datetime(2026, 7, 17, 16, 14, 59)


def test_send_order_rejected_quote_only():
    engine = EventEngine(interval=10)
    gateway = StockGateway(engine, "STOCK")
    result = gateway.send_order(
        OrderRequest(
            symbol="600519",
            exchange=Exchange.SSE,
            direction=Direction.LONG,
            type=OrderType.LIMIT,
            volume=100,
            price=1000,
        )
    )
    assert result == ""


@pytest.mark.network
def test_connect_and_poll_real():
    """需要能访问 qt.gtimg.cn 的网络；默认跳过。"""
    engine = EventEngine(interval=10)
    ticks = []
    contracts = []
    engine.register(EVENT_TICK, lambda e: ticks.append(e.data))
    engine.register(EVENT_CONTRACT, lambda e: contracts.append(e.data))
    engine.start()

    gateway = StockGateway(engine, "STOCK")
    try:
        gateway.connect({"poll_interval": 1.0})
        gateway.subscribe(
            SubscribeRequest(symbol="600519", exchange=Exchange.SSE)
        )
        time.sleep(4)
    finally:
        gateway.close()
        engine.stop()

    assert len(contracts) >= 1  # 订阅时惰性注册
    # 非交易时段时间戳不会前进，因此最多只会收到一条 tick；
    # 交易时段则会收到多条。
    assert len(ticks) >= 1


@pytest.mark.network
def test_query_history_real():
    engine = EventEngine(interval=10)
    gateway = StockGateway(engine, "STOCK")
    bars = gateway.query_history(
        HistoryRequest(
            symbol="600519",
            exchange=Exchange.SSE,
            start=datetime(2026, 6, 1),
            interval=Interval.DAILY,
        )
    )
    assert len(bars) > 10
    assert all(b.high_price >= b.low_price for b in bars)
    assert bars[0].datetime < bars[-1].datetime
