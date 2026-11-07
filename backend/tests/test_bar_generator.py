"""BarGenerator 的 tick 转 bar 与 bar 窗口聚合测试。"""

from datetime import datetime

from app.core.constant import Exchange, Interval
from app.core.object import BarData, TickData
from app.core.strategy.bar_generator import BarGenerator


def make_tick(dt: datetime, price: float, volume: float = 0) -> TickData:
    return TickData(
        gateway_name="T",
        symbol="X",
        exchange=Exchange.LOCAL,
        datetime=dt,
        last_price=price,
        high_price=price,
        low_price=price,
        volume=volume,
    )


def make_bar(dt: datetime, o=10, h=12, low=9, c=11, v=100) -> BarData:
    return BarData(
        gateway_name="T",
        symbol="X",
        exchange=Exchange.LOCAL,
        datetime=dt,
        interval=Interval.MINUTE,
        open_price=o,
        high_price=h,
        low_price=low,
        close_price=c,
        volume=v,
    )


def test_tick_to_minute_bar():
    bars: list[BarData] = []
    bg = BarGenerator(on_bar=bars.append)

    # 第 0 分钟内三笔 tick，随后第 1 分钟的一笔 tick 使该 bar 收线
    bg.update_tick(make_tick(datetime(2026, 1, 5, 9, 30, 1), 100, volume=10))
    bg.update_tick(make_tick(datetime(2026, 1, 5, 9, 30, 20), 103, volume=25))
    bg.update_tick(make_tick(datetime(2026, 1, 5, 9, 30, 50), 99, volume=40))
    bg.update_tick(make_tick(datetime(2026, 1, 5, 9, 31, 2), 101, volume=50))

    assert len(bars) == 1
    bar = bars[0]
    assert bar.open_price == 100
    assert bar.high_price == 103
    assert bar.low_price == 99
    assert bar.close_price == 99
    # 成交量基于累计值差分：(25-10)+(40-25) = 30
    assert bar.volume == 30
    assert bar.datetime == datetime(2026, 1, 5, 9, 30)


def test_zero_price_tick_filtered():
    bars: list[BarData] = []
    bg = BarGenerator(on_bar=bars.append)
    bg.update_tick(make_tick(datetime(2026, 1, 5, 9, 30, 1), 0))
    assert bg.bar is None


def test_generate_flushes_partial_bar():
    bars: list[BarData] = []
    bg = BarGenerator(on_bar=bars.append)
    bg.update_tick(make_tick(datetime(2026, 1, 5, 9, 30, 1), 100))
    result = bg.generate()
    assert result is not None
    assert len(bars) == 1
    assert bg.bar is None


def test_five_minute_window():
    window_bars: list[BarData] = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=5,
        on_window_bar=window_bars.append,
        interval=Interval.MINUTE,
    )

    # 第 0-4 分钟：在 (minute+1)%5==0 即第 4 分钟收线
    prices = [(10, 11, 9), (11, 12, 10), (12, 13, 11), (11, 12, 10), (13, 14, 12)]
    for minute, (o, h, low) in enumerate(prices):
        bg.update_bar(
            make_bar(datetime(2026, 1, 5, 9, minute), o=o, h=h, low=low, c=o, v=10)
        )

    assert len(window_bars) == 1
    wb = window_bars[0]
    assert wb.open_price == 10
    assert wb.high_price == 14
    assert wb.low_price == 9
    assert wb.close_price == 13
    assert wb.volume == 50
    assert wb.datetime == datetime(2026, 1, 5, 9, 0)


def test_hour_window():
    hour_bars: list[BarData] = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=1,
        on_window_bar=hour_bars.append,
        interval=Interval.HOUR,
    )

    # 9 点的第 0..59 分钟 -> 小时 bar 在第 59 分钟推送
    for minute in range(60):
        bg.update_bar(make_bar(datetime(2026, 1, 5, 9, minute), v=1))

    assert len(hour_bars) == 1
    assert hour_bars[0].volume == 60
    assert hour_bars[0].datetime == datetime(2026, 1, 5, 9, 0)


def test_hour_window_cross_hour_without_minute59():
    """跨小时出现缺口（例如缺失第 59 分钟）时仍能正确收线。"""
    hour_bars: list[BarData] = []
    bg = BarGenerator(
        on_bar=lambda b: None,
        window=1,
        on_window_bar=hour_bars.append,
        interval=Interval.HOUR,
    )
    bg.update_bar(make_bar(datetime(2026, 1, 5, 9, 30), v=1))
    bg.update_bar(make_bar(datetime(2026, 1, 5, 9, 45), v=1))
    # 下一个小时到来时没有 :59 的 bar
    bg.update_bar(make_bar(datetime(2026, 1, 5, 10, 0), v=1))

    assert len(hour_bars) == 1
    assert hour_bars[0].volume == 2


def test_daily_window_requires_end_time():
    import pytest

    with pytest.raises(ValueError):
        BarGenerator(
            on_bar=lambda b: None,
            window=1,
            on_window_bar=lambda b: None,
            interval=Interval.DAILY,
        )
