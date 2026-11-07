"""OffsetConverter 测试（SHFE/INE 平今/平昨拆分）。"""

from app.core.constant import Direction, Exchange, Offset, OrderType, Product
from app.core.converter import OffsetConverter
from app.core.object import (
    ContractData,
    OrderRequest,
    PositionData,
    TradeData,
)


def make_contract(exchange: Exchange = Exchange.SHFE) -> ContractData:
    return ContractData(
        gateway_name="CTP",
        symbol="rb2510",
        exchange=exchange,
        name="rb2510",
        product=Product.FUTURES,
        size=10,
        pricetick=1,
    )


def make_converter(exchange: Exchange = Exchange.SHFE) -> OffsetConverter:
    converter = OffsetConverter("CTP")
    converter.update_contract(make_contract(exchange))
    return converter


def set_position(
    converter: OffsetConverter,
    direction: Direction,
    volume: float,
    yd_volume: float,
    exchange: Exchange = Exchange.SHFE,
) -> None:
    converter.update_position(
        PositionData(
            gateway_name="CTP",
            symbol="rb2510",
            exchange=exchange,
            direction=direction,
            volume=volume,
            yd_volume=yd_volume,
        )
    )


def close_req(volume: float, exchange: Exchange = Exchange.SHFE) -> OrderRequest:
    return OrderRequest(
        symbol="rb2510",
        exchange=exchange,
        direction=Direction.SHORT,  # 平掉一个 LONG（多头）持仓
        type=OrderType.LIMIT,
        volume=volume,
        price=3100,
        offset=Offset.CLOSE,
    )


def test_open_request_passthrough():
    converter = make_converter()
    req = OrderRequest(
        symbol="rb2510",
        exchange=Exchange.SHFE,
        direction=Direction.LONG,
        type=OrderType.LIMIT,
        volume=1,
        price=3100,
        offset=Offset.OPEN,
    )
    result = converter.convert_order_request(req)
    assert result == [req]


def test_shfe_close_today_only():
    """总持仓 5，昨仓 2 -> 今仓 3；平 3 手 = 单条 CLOSETODAY（平今）。"""
    converter = make_converter()
    set_position(converter, Direction.LONG, volume=5, yd_volume=2)

    result = converter.convert_order_request(close_req(3))
    assert len(result) == 1
    assert result[0].offset == Offset.CLOSETODAY
    assert result[0].volume == 3


def test_shfe_close_split_today_and_yesterday():
    """今仓可用 3 时平 4 手 -> CLOSETODAY（平今）3 + CLOSEYESTERDAY（平昨）1。"""
    converter = make_converter()
    set_position(converter, Direction.LONG, volume=5, yd_volume=2)

    result = converter.convert_order_request(close_req(4))
    assert len(result) == 2
    assert result[0].offset == Offset.CLOSETODAY
    assert result[0].volume == 3
    assert result[1].offset == Offset.CLOSEYESTERDAY
    assert result[1].volume == 1


def test_shfe_close_insufficient_position_rejected():
    converter = make_converter()
    set_position(converter, Direction.LONG, volume=2, yd_volume=0)

    result = converter.convert_order_request(close_req(5))
    assert result == []


def test_non_shfe_close_passthrough():
    """DCE 等交易所不需要平今拆分。"""
    converter = make_converter(Exchange.DCE)
    set_position(
        converter, Direction.LONG, volume=5, yd_volume=2, exchange=Exchange.DCE
    )

    req = close_req(4, exchange=Exchange.DCE)
    result = converter.convert_order_request(req)
    assert result == [req]


def test_net_position_contract_skips_conversion():
    converter = OffsetConverter("CTP")
    contract = make_contract()
    contract.net_position = True
    converter.update_contract(contract)
    set_position(converter, Direction.LONG, volume=5, yd_volume=2)

    req = close_req(4)
    result = converter.convert_order_request(req)
    assert result == [req]


def test_trade_updates_today_position():
    """一笔 OPEN（开仓）成交会增加今仓数量，从而影响拆分结果。"""
    converter = make_converter()
    set_position(converter, Direction.LONG, volume=2, yd_volume=2)

    # 今日再开仓 3 手
    converter.update_trade(
        TradeData(
            gateway_name="CTP",
            symbol="rb2510",
            exchange=Exchange.SHFE,
            orderid="1",
            tradeid="t1",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            price=3100,
            volume=3,
        )
    )

    holding = converter.get_position_holding("rb2510.SHFE")
    assert holding is not None
    assert holding.long_pos == 5
    assert holding.long_td == 3
    assert holding.long_yd == 2

    # 此时平 4 手会拆分为今仓 3 + 昨仓 1
    result = converter.convert_order_request(close_req(4))
    assert [r.offset for r in result] == [
        Offset.CLOSETODAY,
        Offset.CLOSEYESTERDAY,
    ]


def test_frozen_volume_from_active_order():
    """一笔活动的平仓委托会冻结数量，影响后续请求。"""
    converter = make_converter()
    set_position(converter, Direction.LONG, volume=5, yd_volume=0)

    # 一笔 4 手的挂单平仓委托会冻结这 4 手
    req1 = close_req(4)
    converter.update_order_request(req1, "CTP.1")

    # 仅剩 1 手可用 -> 平 2 手必须被拒绝
    result = converter.convert_order_request(close_req(2))
    assert result == []

    # 平 1 手没问题
    result = converter.convert_order_request(close_req(1))
    assert len(result) == 1
    assert result[0].volume == 1


def test_lock_mode_opens_to_hedge():
    """DCE 有今仓时：锁仓模式会开反方向仓位。"""
    converter = make_converter(Exchange.DCE)
    set_position(
        converter, Direction.LONG, volume=3, yd_volume=0, exchange=Exchange.DCE
    )

    req = close_req(2, exchange=Exchange.DCE)
    result = converter.convert_order_request(req, lock=True)
    assert len(result) == 1
    assert result[0].offset == Offset.OPEN
