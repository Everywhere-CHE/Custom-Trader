"""领域数据类与 ORM 行之间的显式转换器。

领域对象（``app.core.object``）不涉及 SQLAlchemy 相关内容；
这些纯函数生成用于插入或更新的普通字典，并从 ORM 行重建领域
对象（复合 vt_* ID 会在 __post_init__ 中重新生成）。
"""

from ..core.constant import (
    Direction,
    Exchange,
    Interval,
    Offset,
    OrderType,
    Status,
)
from ..core.object import (
    AccountData,
    BarData,
    OrderData,
    PositionData,
    TickData,
    TradeData,
)
from ..core.utility import convert_tz
from .models import BarModel


def account_to_row(account: AccountData) -> dict:
    return {
        "gateway_name": account.gateway_name,
        "accountid": account.accountid,
        "balance": account.balance,
        "frozen": account.frozen,
    }


def order_to_row(order: OrderData) -> dict:
    return {
        "gateway_name": order.gateway_name,
        "orderid": order.orderid,
        "symbol": order.symbol,
        "exchange": order.exchange.value,
        "type": order.type.value,
        "direction": order.direction.value if order.direction else None,
        "offset": order.offset.value,
        "price": order.price,
        "volume": order.volume,
        "traded": order.traded,
        "status": order.status.value,
        "datetime": convert_tz(order.datetime) if order.datetime else None,
        "reference": order.reference,
    }


def trade_to_row(trade: TradeData) -> dict:
    return {
        "gateway_name": trade.gateway_name,
        "tradeid": trade.tradeid,
        "orderid": trade.orderid,
        "symbol": trade.symbol,
        "exchange": trade.exchange.value,
        "direction": trade.direction.value if trade.direction else None,
        "offset": trade.offset.value,
        "price": trade.price,
        "volume": trade.volume,
        "datetime": convert_tz(trade.datetime) if trade.datetime else None,
    }


def position_to_row(position: PositionData) -> dict:
    return {
        "gateway_name": position.gateway_name,
        "symbol": position.symbol,
        "exchange": position.exchange.value,
        "direction": position.direction.value,
        "volume": position.volume,
        "frozen": position.frozen,
        "price": position.price,
        "pnl": position.pnl,
        "yd_volume": position.yd_volume,
    }


def tick_to_row(tick: TickData) -> dict:
    row = {
        "symbol": tick.symbol,
        "exchange": tick.exchange.value,
        "datetime": convert_tz(tick.datetime) if tick.datetime else None,
        "name": tick.name,
        "volume": tick.volume,
        "turnover": tick.turnover,
        "open_interest": tick.open_interest,
        "last_price": tick.last_price,
        "limit_up": tick.limit_up,
        "limit_down": tick.limit_down,
        "open_price": tick.open_price,
        "high_price": tick.high_price,
        "low_price": tick.low_price,
        "pre_close": tick.pre_close,
    }
    for level in range(1, 6):
        for prefix in ("bid_price", "ask_price", "bid_volume", "ask_volume"):
            key = f"{prefix}_{level}"
            row[key] = getattr(tick, key)
    return row


def bar_to_row(bar: BarData) -> dict:
    return {
        "symbol": bar.symbol,
        "exchange": bar.exchange.value,
        "interval": bar.interval.value if bar.interval else "",
        "datetime": convert_tz(bar.datetime) if bar.datetime else None,
        "volume": bar.volume,
        "turnover": bar.turnover,
        "open_interest": bar.open_interest,
        "open_price": bar.open_price,
        "high_price": bar.high_price,
        "low_price": bar.low_price,
        "close_price": bar.close_price,
    }


def contract_to_row(contract) -> dict:  # noqa: ANN001
    return {
        "gateway_name": contract.gateway_name,
        "symbol": contract.symbol,
        "exchange": contract.exchange.value,
        "name": contract.name,
        "product": contract.product.value,
        "size": contract.size,
        "pricetick": contract.pricetick,
        "min_volume": contract.min_volume,
    }


def row_to_bar(row: BarModel, gateway_name: str = "DB") -> BarData:
    return BarData(
        gateway_name=gateway_name,
        symbol=row.symbol,
        exchange=Exchange(row.exchange),
        datetime=row.datetime,
        interval=Interval(row.interval) if row.interval else None,
        volume=row.volume,
        turnover=row.turnover,
        open_interest=row.open_interest,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
    )


def row_to_order(row) -> OrderData:  # noqa: ANN001
    return OrderData(
        gateway_name=row.gateway_name,
        symbol=row.symbol,
        exchange=Exchange(row.exchange),
        orderid=row.orderid,
        type=OrderType(row.type),
        direction=Direction(row.direction) if row.direction else None,
        offset=Offset(row.offset),
        price=row.price,
        volume=row.volume,
        traded=row.traded,
        status=Status(row.status),
        datetime=row.datetime,
        reference=row.reference,
    )


def row_to_trade(row) -> TradeData:  # noqa: ANN001
    return TradeData(
        gateway_name=row.gateway_name,
        symbol=row.symbol,
        exchange=Exchange(row.exchange),
        orderid=row.orderid,
        tradeid=row.tradeid,
        direction=Direction(row.direction) if row.direction else None,
        offset=Offset(row.offset),
        price=row.price,
        volume=row.volume,
        datetime=row.datetime,
    )
