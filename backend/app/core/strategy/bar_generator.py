"""K线生成器：由 Tick 合成 K线，并把 K线 聚合成更大的时间窗口。

移植自 vn.py ``vnpy/trader/utility.py`` 的 BarGenerator：
1. 由 Tick 数据生成 1 分钟 K线；
2. 由 1 分钟 K线 生成 x 分钟 / x 小时 / 日线 K线。

注意：
1. x 分钟 K线 中，x 必须能整除 60：2、3、5、6、10、15、20、30
2. x 小时 K线 中，x 可以是任意数字
"""

from collections.abc import Callable
from datetime import datetime, time

from ..constant import Interval
from ..object import BarData, TickData


class BarGenerator:
    """Tick -> 1 分钟 K线 -> 窗口 K线 合成器。"""

    def __init__(
        self,
        on_bar: Callable,
        window: int = 0,
        on_window_bar: Callable | None = None,
        interval: Interval = Interval.MINUTE,
        daily_end: time | None = None,
        on_save_bar: Callable | None = None,  # 可选：把 K线 持久化到数据库
    ) -> None:
        self.bar: BarData | None = None
        self.on_bar: Callable = on_bar

        self.interval: Interval = interval
        self.interval_count: int = 0

        self.hour_bar: BarData | None = None
        self.daily_bar: BarData | None = None

        self.window: int = window
        self.window_bar: BarData | None = None
        self.on_window_bar: Callable | None = on_window_bar

        self.last_tick: TickData | None = None

        self.daily_end: time | None = daily_end
        if self.interval == Interval.DAILY and not self.daily_end:
            raise ValueError("daily_end time is required for daily bars")

        self.on_save_bar: Callable | None = on_save_bar

    def update_tick(self, tick: TickData) -> None:
        """向生成器推入一个新 Tick。"""
        new_minute: bool = False

        # 过滤最新价为 0 的 Tick 数据
        if not tick.last_price:
            return
        assert tick.datetime is not None

        if not self.bar:
            new_minute = True
        elif self.bar.datetime is not None and (
            (self.bar.datetime.minute != tick.datetime.minute)
            or (self.bar.datetime.hour != tick.datetime.hour)
        ):
            self.bar.datetime = self.bar.datetime.replace(
                second=0, microsecond=0
            )
            self.on_bar(self.bar)
            if self.on_save_bar:
                self.on_save_bar(self.bar)

            new_minute = True

        if new_minute:
            self.bar = BarData(
                symbol=tick.symbol,
                exchange=tick.exchange,
                interval=Interval.MINUTE,
                datetime=tick.datetime,
                gateway_name=tick.gateway_name,
                open_price=tick.last_price,
                high_price=tick.last_price,
                low_price=tick.last_price,
                close_price=tick.last_price,
                open_interest=tick.open_interest,
            )
        elif self.bar:
            self.bar.high_price = max(self.bar.high_price, tick.last_price)
            if self.last_tick and tick.high_price > self.last_tick.high_price:
                self.bar.high_price = max(self.bar.high_price, tick.high_price)

            self.bar.low_price = min(self.bar.low_price, tick.last_price)
            if self.last_tick and tick.low_price < self.last_tick.low_price:
                self.bar.low_price = min(self.bar.low_price, tick.low_price)

            self.bar.close_price = tick.last_price
            self.bar.open_interest = tick.open_interest
            self.bar.datetime = tick.datetime

        if self.last_tick and self.bar:
            volume_change: float = tick.volume - self.last_tick.volume
            self.bar.volume += max(volume_change, 0)

            turnover_change: float = tick.turnover - self.last_tick.turnover
            self.bar.turnover += max(turnover_change, 0)

        self.last_tick = tick

    def update_bar(self, bar: BarData) -> None:
        """向生成器推入一根 1 分钟 K线。"""
        if self.interval == Interval.MINUTE:
            self.update_bar_minute_window(bar)
        elif self.interval == Interval.HOUR:
            self.update_bar_hour_window(bar)
        else:
            self.update_bar_daily_window(bar)

    def update_bar_minute_window(self, bar: BarData) -> None:
        """把 1 分钟 K线 聚合成 x 分钟窗口 K线。"""
        assert bar.datetime is not None
        if not self.window_bar:
            dt: datetime = bar.datetime.replace(second=0, microsecond=0)
            self.window_bar = BarData(
                symbol=bar.symbol,
                exchange=bar.exchange,
                datetime=dt,
                gateway_name=bar.gateway_name,
                open_price=bar.open_price,
                high_price=bar.high_price,
                low_price=bar.low_price,
            )
        else:
            self.window_bar.high_price = max(
                self.window_bar.high_price, bar.high_price
            )
            self.window_bar.low_price = min(
                self.window_bar.low_price, bar.low_price
            )

        self.window_bar.close_price = bar.close_price
        self.window_bar.volume += bar.volume
        self.window_bar.turnover += bar.turnover
        self.window_bar.open_interest = bar.open_interest

        # 当 (分钟数+1) 能被窗口大小整除时，窗口结束
        if not (bar.datetime.minute + 1) % self.window:
            if self.on_window_bar:
                self.on_window_bar(self.window_bar)
            self.window_bar = None

    def update_bar_hour_window(self, bar: BarData) -> None:
        """把 1 分钟 K线 聚合成小时 K线。"""
        assert bar.datetime is not None
        if not self.hour_bar:
            dt = bar.datetime.replace(minute=0, second=0, microsecond=0)
            self.hour_bar = self._copy_into_window(bar, dt)
            return

        finished_bar: BarData | None = None

        # 第 59 分钟收盘则该小时结束
        if bar.datetime.minute == 59:
            self._merge_into(self.hour_bar, bar)
            finished_bar = self.hour_bar
            self.hour_bar = None
        # 新小时的 K线 到来时推送上一根小时 K线
        elif (
            self.hour_bar.datetime is not None
            and bar.datetime.hour != self.hour_bar.datetime.hour
        ):
            finished_bar = self.hour_bar
            dt = bar.datetime.replace(minute=0, second=0, microsecond=0)
            self.hour_bar = self._copy_into_window(bar, dt)
        else:
            self._merge_into(self.hour_bar, bar)

        if finished_bar:
            self.on_hour_bar(finished_bar)

    def on_hour_bar(self, bar: BarData) -> None:
        """处理一根已完成的小时 K线（聚合成 x 小时窗口）。"""
        if self.window == 1:
            if self.on_window_bar:
                self.on_window_bar(bar)
        else:
            if not self.window_bar:
                self.window_bar = BarData(
                    symbol=bar.symbol,
                    exchange=bar.exchange,
                    datetime=bar.datetime,
                    gateway_name=bar.gateway_name,
                    open_price=bar.open_price,
                    high_price=bar.high_price,
                    low_price=bar.low_price,
                )
            else:
                self.window_bar.high_price = max(
                    self.window_bar.high_price, bar.high_price
                )
                self.window_bar.low_price = min(
                    self.window_bar.low_price, bar.low_price
                )

            self.window_bar.close_price = bar.close_price
            self.window_bar.volume += bar.volume
            self.window_bar.turnover += bar.turnover
            self.window_bar.open_interest = bar.open_interest

            self.interval_count += 1
            if not self.interval_count % self.window:
                self.interval_count = 0
                if self.on_window_bar:
                    self.on_window_bar(self.window_bar)
                self.window_bar = None

    def update_bar_daily_window(self, bar: BarData) -> None:
        """把 1 分钟 K线 聚合成日线 K线（在 daily_end 时刻收盘）。"""
        assert bar.datetime is not None
        if not self.daily_bar:
            self.daily_bar = BarData(
                symbol=bar.symbol,
                exchange=bar.exchange,
                datetime=bar.datetime,
                gateway_name=bar.gateway_name,
                open_price=bar.open_price,
                high_price=bar.high_price,
                low_price=bar.low_price,
            )
        else:
            self.daily_bar.high_price = max(
                self.daily_bar.high_price, bar.high_price
            )
            self.daily_bar.low_price = min(
                self.daily_bar.low_price, bar.low_price
            )

        self.daily_bar.close_price = bar.close_price
        self.daily_bar.volume += bar.volume
        self.daily_bar.turnover += bar.turnover
        self.daily_bar.open_interest = bar.open_interest

        if bar.datetime.time() == self.daily_end:
            assert self.daily_bar.datetime is not None
            self.daily_bar.datetime = bar.datetime.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            if self.on_window_bar:
                self.on_window_bar(self.daily_bar)
            self.daily_bar = None

    def generate(self) -> BarData | None:
        """立即推送当前未完成的分钟 K线。"""
        bar: BarData | None = self.bar

        if bar and bar.datetime is not None:
            bar.datetime = bar.datetime.replace(second=0, microsecond=0)
            self.on_bar(bar)

        self.bar = None
        return bar

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_into_window(bar: BarData, dt: datetime) -> BarData:
        return BarData(
            symbol=bar.symbol,
            exchange=bar.exchange,
            datetime=dt,
            gateway_name=bar.gateway_name,
            open_price=bar.open_price,
            high_price=bar.high_price,
            low_price=bar.low_price,
            close_price=bar.close_price,
            volume=bar.volume,
            turnover=bar.turnover,
            open_interest=bar.open_interest,
        )

    @staticmethod
    def _merge_into(window_bar: BarData, bar: BarData) -> None:
        window_bar.high_price = max(window_bar.high_price, bar.high_price)
        window_bar.low_price = min(window_bar.low_price, bar.low_price)
        window_bar.close_price = bar.close_price
        window_bar.volume += bar.volume
        window_bar.turnover += bar.turnover
        window_bar.open_interest = bar.open_interest
