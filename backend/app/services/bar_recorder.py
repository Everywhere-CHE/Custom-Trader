"""K 线聚合服务：将实时 tick 聚合成 1 分钟 K 线并落库。

从 DataEngine 抽出的**聚合**职责。分离后：

- DataEngine（``db/data_engine.py``）只做原始事件持久化
  （tick 缓冲批量落库 + 订单/成交/持仓/账户/合约 upsert），
  不再承担 K 线聚合；
- BarRecorder（本模块）专注 tick→1m 聚合，且**不受
  ``persist_ticks`` 开关门控**——即便不持久化 tick，1m K 线
  仍会生成（修复了旧代码中 ``persist_ticks=False`` 时连 1m 线
  都不产生的耦合）。

完成后（分钟边界或关闭时）经 DataEngine.save_bar 落库，保持单一
持久化路径。运行在事件分发线程内，同步 ORM 安全。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.constant import Interval
from ..core.engine.base import BaseEngine
from ..core.event import EVENT_TICK, Event, EventEngine
from ..core.object import BarData, TickData
from ..db.data_engine import DataEngine

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine


class BarRecorder(BaseEngine):
    """将实时 tick 聚合成 1m K 线并持久化。"""

    def __init__(self, main_engine: "MainEngine", event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, "bar_recorder")
        # vt_symbol -> 当前正在构建的 1m K 线
        self._current_bars: dict[str, BarData] = {}
        self.register_event()

    def register_event(self) -> None:
        self.event_engine.register(EVENT_TICK, self.process_tick_event)

    def process_tick_event(self, event: Event) -> None:
        """把 tick 聚合成 1m K 线；分钟边界时落库上一根。"""
        tick: TickData = event.data
        if not tick.last_price:
            return
        key = tick.vt_symbol
        bar = self._current_bars.get(key)
        if bar is None or bar.datetime.minute != tick.datetime.minute:
            # 保存已完成的 K 线
            if bar is not None and bar.volume > 0:
                self._save_bar(bar)
            # 开始一根新的 K 线
            bar_dt = tick.datetime.replace(second=0, microsecond=0)
            bar = BarData(
                gateway_name=tick.gateway_name,
                symbol=tick.symbol,
                exchange=tick.exchange,
                datetime=bar_dt,
                interval=Interval.MINUTE,
                volume=0,
                open_price=tick.last_price,
                high_price=tick.last_price,
                low_price=tick.last_price,
                close_price=tick.last_price,
            )
            self._current_bars[key] = bar
        # 更新 OHLC
        bar.high_price = max(bar.high_price, tick.last_price)
        bar.low_price = min(bar.low_price, tick.last_price)
        bar.close_price = tick.last_price
        bar.volume += tick.volume if tick.volume else 0

    def _save_bar(self, bar: BarData) -> None:
        """通过 DataEngine 落库（单一持久化路径）。"""
        data_engine = self.main_engine.get_engine("data")
        if isinstance(data_engine, DataEngine):
            data_engine.save_bar(bar)

    def close(self) -> None:
        """关闭时刷新所有未完成的 K 线。"""
        for key, bar in list(self._current_bars.items()):
            if bar.volume > 0:
                self._save_bar(bar)
            del self._current_bars[key]

    def get_current_bar(self, vt_symbol: str) -> BarData | None:
        """返回当前正在构建的 1m K 线（内存中的实时分钟，volume>0 才有效）。

        配合 :meth:`DataEngine.load_bars_tail` 使用——图表 1m 图用此方法
        替代 ``load_intraday_bars``，避免每次重扫 tick（纯读优化）。
        """
        bar = self._current_bars.get(vt_symbol)
        if bar is not None and bar.volume > 0:
            return bar
        return None
