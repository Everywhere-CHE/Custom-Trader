"""数据引擎：将事件持久化到数据库。

位于 ``db`` 包（而非 ``core``）中，从而让量化核心不依赖
SQLAlchemy。运行在事件分发线程内，因此使用同步 ORM 是安全的。

持久化策略：
- orders / trades：立即插入或更新（频率低、对审计至关重要）；
- ticks：先在内存中缓冲，在每次定时器事件（1 秒）时批量插入；
- bars：K 线读写接口（save_bars / load_bars / load_intraday_bars）；
  实时 tick→1m 聚合已抽到独立的 BarRecorder 服务
  （见 services/bar_recorder.py），本引擎不再承担聚合职责；
- positions / accounts / contracts：按事件插入或更新（快照式）。
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.engine.base import BaseEngine
from ..core.event import (
    EVENT_ACCOUNT,
    EVENT_CONTRACT,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TIMER,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from ..core.object import BarData, TickData
from ..core.constant import Exchange, Interval
from ..core.utility import CHINA_TZ, bucket_start, convert_tz
from . import mappers
from .models import (
    AccountModel,
    BarModel,
    ContractModel,
    OrderModel,
    PositionModel,
    TickModel,
    TradeModel,
)
from .session import create_session, get_engine

if TYPE_CHECKING:
    from datetime import datetime

    from ..core.engine.main_engine import MainEngine


class DataEngine(BaseEngine):
    """将平台数据持久化到关系型数据库。"""

    def __init__(
        self,
        main_engine: "MainEngine",
        event_engine: EventEngine,
    ) -> None:
        super().__init__(main_engine, event_engine, "data")

        settings = get_settings()
        self.persist_ticks: bool = settings.persist_ticks
        self._tick_buffer: list[TickData] = []
        self._is_sqlite: bool = get_engine().dialect.name == "sqlite"

        self.register_event()

    def register_event(self) -> None:
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_POSITION, self.process_position_event)
        self.event_engine.register(EVENT_ACCOUNT, self.process_account_event)
        self.event_engine.register(EVENT_CONTRACT, self.process_contract_event)
        if self.persist_ticks:
            self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    # ------------------------------------------------------------------
    # 插入或更新辅助方法
    # ------------------------------------------------------------------

    def _upsert(
        self,
        session: Session,
        model,  # noqa: ANN001
        row: dict,
        index_elements: list[str],
    ) -> None:
        """区分方言的插入或更新（sqlite / postgresql 共用同一 API）。"""
        if self._is_sqlite:
            stmt = sqlite_insert(model).values(**row)
        else:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(model).values(**row)
        update_cols = {
            k: v for k, v in row.items() if k not in index_elements
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements, set_=update_cols
        )
        session.execute(stmt)

    # ------------------------------------------------------------------
    # 事件处理器（运行在事件分发线程内）
    # ------------------------------------------------------------------

    def process_order_event(self, event: Event) -> None:
        session = create_session()
        try:
            self._upsert(
                session,
                OrderModel,
                mappers.order_to_row(event.data),
                ["gateway_name", "orderid"],
            )
            session.commit()
        finally:
            session.close()

    def process_trade_event(self, event: Event) -> None:
        session = create_session()
        try:
            self._upsert(
                session,
                TradeModel,
                mappers.trade_to_row(event.data),
                ["gateway_name", "tradeid"],
            )
            session.commit()
        finally:
            session.close()

    def process_position_event(self, event: Event) -> None:
        session = create_session()
        try:
            self._upsert(
                session,
                PositionModel,
                mappers.position_to_row(event.data),
                ["gateway_name", "symbol", "exchange", "direction"],
            )
            session.commit()
        finally:
            session.close()

    def process_account_event(self, event: Event) -> None:
        session = create_session()
        try:
            self._upsert(
                session,
                AccountModel,
                mappers.account_to_row(event.data),
                ["gateway_name", "accountid"],
            )
            session.commit()
        finally:
            session.close()

    def process_contract_event(self, event: Event) -> None:
        session = create_session()
        try:
            self._upsert(
                session,
                ContractModel,
                mappers.contract_to_row(event.data),
                ["symbol", "exchange", "gateway_name"],
            )
            session.commit()
        finally:
            session.close()

    def process_tick_event(self, event: Event) -> None:
        """缓冲 tick；由定时器事件批量落库。

        实时 tick→1m 聚合已抽到独立的 BarRecorder 服务（见
        services/bar_recorder.py），本引擎只负责 tick 的持久化。
        """
        tick: TickData = event.data
        self._tick_buffer.append(tick)

    def process_timer_event(self, event: Event) -> None:
        """每秒刷新一次 tick 缓冲区。"""
        if not self._tick_buffer:
            return
        buffer, self._tick_buffer = self._tick_buffer, []

        session = create_session()
        try:
            for tick in buffer:
                self._upsert(
                    session,
                    TickModel,
                    mappers.tick_to_row(tick),
                    ["symbol", "exchange", "datetime"],
                )
            session.commit()
        finally:
            session.close()

    # ------------------------------------------------------------------
    # K 线数据接口（签名与 vn.py BaseDatabase 保持一致，
    # 以便在第三阶段抽取出合适的抽象层）
    # ------------------------------------------------------------------

    def save_bar(self, bar: BarData) -> None:
        """将单根 K 线持久化到数据库。"""
        self.save_bars([bar])

    def save_bars(self, bars: list[BarData]) -> bool:
        session = create_session()
        try:
            for bar in bars:
                self._upsert(
                    session,
                    BarModel,
                    mappers.bar_to_row(bar),
                    ["symbol", "exchange", "interval", "datetime"],
                )
            session.commit()
            return True
        finally:
            session.close()

    def load_bars(
        self,
        symbol: str,
        exchange: Exchange,
        interval: Interval,
        start: "datetime | None" = None,
        end: "datetime | None" = None,
    ) -> list[BarData]:
        session = create_session()
        try:
            stmt = (
                select(BarModel)
                .where(
                    BarModel.symbol == symbol,
                    BarModel.exchange == exchange.value,
                    BarModel.interval == interval.value,
                )
                .order_by(BarModel.datetime)
            )
            if start:
                stmt = stmt.where(BarModel.datetime >= convert_tz(start))
            if end:
                stmt = stmt.where(BarModel.datetime <= convert_tz(end))
            rows = session.scalars(stmt).all()
            return [mappers.row_to_bar(row) for row in rows]
        finally:
            session.close()

    def load_bars_tail(
        self,
        symbol: str,
        exchange: Exchange,
        interval: Interval,
        limit: int = 500,
    ) -> list[BarData]:
        """读取最近 ``limit`` 根 K 线（从 bars 表尾部取，快于全量读）。

        配合 :meth:`BarRecorder.get_current_bar` 使用——图表 1m 图用此方法
        替代 ``load_intraday_bars``，避免每次重扫 tick。
        """
        session = create_session()
        try:
            stmt = (
                select(BarModel)
                .where(
                    BarModel.symbol == symbol,
                    BarModel.exchange == exchange.value,
                    BarModel.interval == interval.value,
                )
                .order_by(BarModel.datetime.desc())
                .limit(limit)
            )
            rows = session.scalars(stmt).all()
            return [mappers.row_to_bar(row) for row in reversed(rows)]
        finally:
            session.close()

    # ------------------------------------------------------------------
    # 历史订单/成交查询（跨重启持久化）
    # ------------------------------------------------------------------

    @staticmethod
    def _bucket_start(dt: "datetime", interval: Interval) -> "datetime":
        """将日期时间对齐到其聚合桶的起始时刻。"""
        from ..core.utility import bucket_start

        return bucket_start(dt, interval)

    def load_intraday_bars(
        self,
        symbol: str,
        exchange: Exchange,
        start: "datetime | None" = None,
        end: "datetime | None" = None,
        limit: int = 500,
        interval: Interval = Interval.MINUTE,
    ) -> list[BarData]:
        """将真实记录的 tick 聚合成任意周期的 K 线（纯读，不写回）。

        从 ``bars`` 表读真实 K 线 + 读取其后的增量 tick 在内存中聚合成
        尾部，合并返回。**不再把合成 K 线写回 bars 表**（旧版会把
        ``gateway_name="DB"`` 的合成线 upsert 进去，污染真实 K 线存储）；
        因此 ``bars`` 表只保留真实数据（网关下载或 BarRecorder 实时聚合）。
        """
        # 1. 从 bars 表读真实 K 线
        existing = self.load_bars(symbol, exchange, interval, start, end)
        # 修正历史 bars 的成交量：如果 bars 表中的 volume 是累计值（非增量），
        # 则根据相邻 bar 的差值重算。累计值通常远大于合理范围。
        if existing:
            corrected = [existing[0]]
            for i in range(1, len(existing)):
                prev = corrected[-1]
                curr = existing[i]
                # 如果当前 volume 远大于上一个（>100倍），说明是累计值
                if curr.volume > prev.volume * 100 and prev.volume > 0:
                    curr.volume = max(0, curr.volume - prev.volume)
                corrected.append(curr)
            existing = corrected
        last_bar_dt: "datetime | None" = existing[-1].datetime if existing else None

        # 2. 读取 last_bar_dt 之后的 tick（仅增量），聚合并保存
        session = create_session()
        try:
            stmt = (
                select(TickModel)
                .where(
                    TickModel.symbol == symbol,
                    TickModel.exchange == exchange.value,
                )
                .order_by(TickModel.datetime)
            )
            if last_bar_dt:
                stmt = stmt.where(TickModel.datetime > convert_tz(last_bar_dt))
            if start and not last_bar_dt:
                stmt = stmt.where(TickModel.datetime >= convert_tz(start))
            if end:
                stmt = stmt.where(TickModel.datetime <= convert_tz(end))
            rows = session.scalars(stmt).all()
        finally:
            session.close()

        if not rows:
            # 没有新 tick，直接返回已有 bars
            if limit > 0:
                return existing[-limit:]
            return existing

        # 聚合新 tick 为 bars
        new_bars: list[BarData] = []
        current: BarData | None = None
        prev_volume: float | None = None
        for row in rows:
            if not row.last_price or row.datetime is None:
                continue
            bucket = self._bucket_start(row.datetime, interval)
            volume_delta = 0.0
            if prev_volume is not None:
                volume_delta = max(0.0, row.volume - prev_volume)
            prev_volume = row.volume

            if current is None or current.datetime != bucket:
                current = BarData(
                    gateway_name="DB",
                    symbol=symbol,
                    exchange=exchange,
                    datetime=bucket,
                    interval=interval,
                    open_price=row.last_price,
                    high_price=row.last_price,
                    low_price=row.last_price,
                    close_price=row.last_price,
                    volume=volume_delta,
                )
                new_bars.append(current)
            else:
                current.high_price = max(current.high_price, row.last_price)
                current.low_price = min(current.low_price, row.last_price)
                current.close_price = row.last_price
                current.volume += volume_delta

        # 4. 去重：new_bars 的第一条可能与 existing 的最后一条同 bucket
        if existing and new_bars and existing[-1].datetime == new_bars[0].datetime:
            new_bars = new_bars[1:]

        # 5. 合并返回（不再写回 bars 表——旧版会把合成线写进去，污染真实 K 线存储）
        merged = existing + new_bars
        if limit > 0:
            merged = merged[-limit:]
        return merged

    def load_trades(
        self,
        vt_symbol: str = "",
        start: "datetime | None" = None,
        end: "datetime | None" = None,
        limit: int = 500,
    ) -> list:
        """从数据库加载历史成交，按最新在前排序。"""
        session = create_session()
        try:
            stmt = select(TradeModel).order_by(TradeModel.datetime.desc())
            if vt_symbol:
                symbol, _, exchange = vt_symbol.rpartition(".")
                stmt = stmt.where(
                    TradeModel.symbol == symbol,
                    TradeModel.exchange == exchange,
                )
            if start:
                stmt = stmt.where(TradeModel.datetime >= convert_tz(start))
            if end:
                stmt = stmt.where(TradeModel.datetime <= convert_tz(end))
            if limit > 0:
                stmt = stmt.limit(limit)
            rows = session.scalars(stmt).all()
            return [mappers.row_to_trade(row) for row in rows]
        finally:
            session.close()

    def load_orders(
        self,
        vt_symbol: str = "",
        start: "datetime | None" = None,
        end: "datetime | None" = None,
        limit: int = 500,
    ) -> list:
        """从数据库加载历史订单，按最新在前排序。"""
        session = create_session()
        try:
            stmt = select(OrderModel).order_by(OrderModel.datetime.desc())
            if vt_symbol:
                symbol, _, exchange = vt_symbol.rpartition(".")
                stmt = stmt.where(
                    OrderModel.symbol == symbol,
                    OrderModel.exchange == exchange,
                )
            if start:
                stmt = stmt.where(OrderModel.datetime >= convert_tz(start))
            if end:
                stmt = stmt.where(OrderModel.datetime <= convert_tz(end))
            if limit > 0:
                stmt = stmt.limit(limit)
            rows = session.scalars(stmt).all()
            return [mappers.row_to_order(row) for row in rows]
        finally:
            session.close()

    def delete_order(self, vt_orderid: str) -> bool:
        """根据 vt_orderid 从数据库删除单条订单。"""
        session = create_session()
        try:
            gateway_name, _, orderid = vt_orderid.partition(".")
            if not orderid:
                orderid = vt_orderid
            deleted = session.query(OrderModel).filter_by(
                gateway_name=gateway_name,
                orderid=orderid,
            ).delete()
            session.commit()
            return deleted > 0
        finally:
            session.close()

    def delete_trade(self, vt_tradeid: str) -> bool:
        """根据 vt_tradeid 从数据库删除单条成交。"""
        session = create_session()
        try:
            gateway_name, _, tradeid = vt_tradeid.partition(".")
            if not tradeid:
                tradeid = vt_tradeid
            deleted = session.query(TradeModel).filter_by(
                gateway_name=gateway_name,
                tradeid=tradeid,
            ).delete()
            session.commit()
            return deleted > 0
        finally:
            session.close()

    def clear_all_orders(self) -> int:
        """删除数据库中的所有订单记录。"""
        session = create_session()
        try:
            count = session.query(OrderModel).delete()
            session.commit()
            return count
        finally:
            session.close()

    def clear_all_trades(self) -> int:
        """删除数据库中的所有成交记录。"""
        session = create_session()
        try:
            count = session.query(TradeModel).delete()
            session.commit()
            return count
        finally:
            session.close()

    def close(self) -> None:
        """在关闭时刷新所有剩余的 tick（K 线由 BarRecorder 自行刷新）。"""
        self.process_timer_event(Event(EVENT_TIMER))
