"""实盘策略引擎。

将行情 / 订单 / 成交事件路由到各策略实例，管理其生命周期
（添加 / 初始化 / 启动 / 停止 / 编辑 / 删除），执行本地停止单，
并把实例持久化到 ``strategies`` 表。

线程模型：策略回调在事件分发线程中串行执行；初始化在单个
后台工作线程中运行（加载历史数据可能阻塞）；start / stop / edit
由 REST 线程调用，只翻转标志位 / 修改字典（与 vn.py 的
CtaEngine 采用相同的权衡）。
"""

import json
import logging
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ..constant import Direction, Interval, Offset, OrderType
from ..engine.base import BaseEngine
from ..event import (
    EVENT_ORDER,
    EVENT_TIMER,
    EVENT_TICK,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from ..event.type import EVENT_STRATEGY, EVENT_STRATEGY_LOG
from ..object import (
    HistoryRequest,
    OrderRequest,
    SubscribeRequest,
    TickData,
)
from ..utility import extract_vt_symbol, now_cn, round_to
from .loader import load_strategy_classes
from .template import StopOrder, StopOrderStatus, StrategyTemplate

if TYPE_CHECKING:
    from ..engine.main_engine import MainEngine


class StrategyEngine(BaseEngine):
    """CTA 策略引擎（实盘交易）。"""

    def __init__(
        self,
        main_engine: "MainEngine",
        event_engine: EventEngine,
    ) -> None:
        super().__init__(main_engine, event_engine, "strategy")

        self.classes: dict[str, type[StrategyTemplate]] = {}
        self.strategies: dict[str, StrategyTemplate] = {}

        self.symbol_strategy_map: defaultdict[str, list[StrategyTemplate]] = (
            defaultdict(list)
        )
        self.orderid_strategy_map: dict[str, StrategyTemplate] = {}
        self.strategy_orderid_map: defaultdict[str, set[str]] = defaultdict(set)

        # 已处理的成交编号：对重放的成交去重（例如 CTP 重连后
        # 私有流重推），对应 vn.py 的 vt_tradeids。
        self.vt_tradeids: set[str] = set()
        # 在 send_order 注册 orderid 之前就到达的事件（瞬时成交的
        # 网关可能抢在映射更新之前推送）；注册完成后会重放一次。
        self._pending_events: defaultdict[str, list[Event]] = defaultdict(list)

        self.stop_orders: dict[str, StopOrder] = {}
        self.stop_order_count: int = 0

        self._reconcile_counter: int = 0

        self.init_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="StrategyInit"
        )

        self.load_strategy_classes()
        self.register_event()

    # ------------------------------------------------------------------
    # 初始化设置
    # ------------------------------------------------------------------

    def load_strategy_classes(self) -> None:
        user_dir = Path(__file__).resolve().parents[3] / "strategies"
        self.classes = load_strategy_classes(user_dir)

    def register_event(self) -> None:
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    # ------------------------------------------------------------------
    # 事件处理（事件分发线程）
    # ------------------------------------------------------------------

    def process_tick_event(self, event: Event) -> None:
        tick: TickData = event.data
        strategies = self.symbol_strategy_map.get(tick.vt_symbol)
        if not strategies:
            return

        self.check_stop_order(tick)

        for strategy in strategies:
            if strategy.inited:
                self._call(strategy, strategy.on_tick, tick)

    def process_order_event(self, event: Event) -> None:
        order = event.data
        strategy = self.orderid_strategy_map.get(order.vt_orderid)
        if not strategy:
            self._buffer_event(order.vt_orderid, event)
            return

        if not order.is_active():
            self.strategy_orderid_map[strategy.strategy_name].discard(
                order.vt_orderid
            )

        self._call(strategy, strategy.on_order, order)

    def process_trade_event(self, event: Event) -> None:
        trade = event.data
        strategy = self.orderid_strategy_map.get(trade.vt_orderid)
        if not strategy:
            self._buffer_event(trade.vt_orderid, event)
            return

        # 对重放的成交去重（CTP 重连时会重推私有流），
        # 避免 strategy.pos 被重复累加。
        if trade.vt_tradeid in self.vt_tradeids:
            return
        self.vt_tradeids.add(trade.vt_tradeid)

        if trade.direction == Direction.LONG:
            strategy.pos += trade.volume
        else:
            strategy.pos -= trade.volume

        self._call(strategy, strategy.on_trade, trade)
        self.put_strategy_event(strategy)
        # 持久化运行时变量（尤其是 pos！），这样带仓重启后
        # 可以继续交易昨日的持仓。
        self._db_save_variables(strategy)

    def process_timer_event(self, event: Event) -> None:
        """定时器：每 60 秒检查一次策略 pos 与网关持仓是否一致。

        手动交易导致的持仓差异会自动同步（仅限未被多策略共用的合约）。
        """
        self._reconcile_counter += 1
        if self._reconcile_counter < 60:
            return
        self._reconcile_counter = 0

        for strategy in list(self.strategies.values()):
            if not strategy.inited or not strategy.trading:
                continue
            self._auto_reconcile_pos(strategy)

    def _buffer_event(self, vt_orderid: str, event: Event) -> None:
        """暂存策略映射尚未建立的订单 / 成交事件。

        瞬时成交的网关可能在 ``send_order`` 完成 orderid 注册之前
        就推送事件；注册完成后会立即重放这些暂存事件。非策略
        （手动）订单事件也会落到这里，因此缓冲区设有上限。
        """
        self._pending_events[vt_orderid].append(event)
        while len(self._pending_events) > 500:
            self._pending_events.pop(next(iter(self._pending_events)))

    def _replay_pending_events(self, vt_orderid: str) -> None:
        """重新入队那些抢在 orderid 注册之前到达的事件。

        由发单线程调用；把事件重新放回队列可以保证所有策略
        回调仍在事件分发线程中执行。
        """
        for event in self._pending_events.pop(vt_orderid, []):
            self.event_engine.put(event)

    def _call(self, strategy: StrategyTemplate, func, *args) -> bool:  # noqa: ANN001
        """调用策略回调；出现异常时停止该策略。

        回调正常完成（未抛异常）时返回 True。
        """
        try:
            func(*args)
            return True
        except Exception:
            strategy.trading = False
            # 仅 on_init 失败时才重置 inited（运行时回调异常不重置）
            if func.__name__ == "on_init":
                strategy.inited = False
            self.write_strategy_log(
                strategy,
                f"exception in {func.__name__}, strategy stopped:\n"
                f"{traceback.format_exc()}",
                level=logging.ERROR,
            )
            self.put_strategy_event(strategy)
            return False

    # ------------------------------------------------------------------
    # 本地停止单
    # ------------------------------------------------------------------

    def send_stop_order(
        self,
        strategy: StrategyTemplate,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
    ) -> str:
        self.stop_order_count += 1
        stop_order = StopOrder(
            vt_symbol=strategy.vt_symbol,
            direction=direction,
            offset=offset,
            price=price,
            volume=volume,
            stop_orderid=f"STOP.{self.stop_order_count}",
            strategy_name=strategy.strategy_name,
            datetime=now_cn(),
        )
        self.stop_orders[stop_order.stop_orderid] = stop_order
        strategy.on_stop_order(stop_order)
        return stop_order.stop_orderid

    def check_stop_order(self, tick: TickData) -> None:
        """用最新 Tick 检查并触发等待中的停止单。"""
        for stop_order in list(self.stop_orders.values()):
            if stop_order.vt_symbol != tick.vt_symbol:
                continue

            long_triggered = (
                stop_order.direction == Direction.LONG
                and tick.last_price >= stop_order.price
            )
            short_triggered = (
                stop_order.direction == Direction.SHORT
                and tick.last_price <= stop_order.price
            )
            if not long_triggered and not short_triggered:
                continue

            self.stop_orders.pop(stop_order.stop_orderid)
            strategy = self.strategies.get(stop_order.strategy_name)
            if not strategy:
                continue

            # 带滑点保护的限价
            contract = self.main_engine.oms.get_contract(stop_order.vt_symbol)
            pricetick = contract.pricetick if contract else 0.01
            if stop_order.direction == Direction.LONG:
                protect = tick.last_price + 5 * pricetick
                if tick.limit_up:
                    protect = min(protect, tick.limit_up)
            else:
                protect = tick.last_price - 5 * pricetick
                if tick.limit_down:
                    protect = max(protect, tick.limit_down)

            stop_order.vt_orderids = self.send_order(
                strategy,
                stop_order.direction,
                stop_order.offset,
                protect,
                stop_order.volume,
                stop=False,
            )
            stop_order.status = StopOrderStatus.TRIGGERED
            self._call(strategy, strategy.on_stop_order, stop_order)

    # ------------------------------------------------------------------
    # 交易接口（由策略调用）
    # ------------------------------------------------------------------

    def send_order(
        self,
        strategy: StrategyTemplate,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        stop: bool,
    ) -> list[str]:
        contract = self.main_engine.oms.get_contract(strategy.vt_symbol)
        if not contract:
            self.write_strategy_log(
                strategy,
                f"send order failed: contract not found {strategy.vt_symbol}",
                level=logging.WARNING,
            )
            return []

        price = round_to(price, contract.pricetick)

        if stop:
            return [
                self.send_stop_order(strategy, direction, offset, price, volume)
            ]

        symbol, exchange = extract_vt_symbol(strategy.vt_symbol)
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            type=OrderType.LIMIT,
            volume=volume,
            price=price,
            offset=offset,
            reference=f"STR.{strategy.strategy_name}",
        )

        # 开平转换（上期所 / 能源中心拆分平今 / 平昨）
        reqs = self.main_engine.oms.convert_order_request(
            req, contract.gateway_name
        )

        vt_orderids: list[str] = []
        for converted in reqs:
            vt_orderid = self.main_engine.send_order(
                converted, contract.gateway_name
            )
            if not vt_orderid:
                continue
            self.main_engine.oms.update_order_request(
                converted, vt_orderid, contract.gateway_name
            )
            self.orderid_strategy_map[vt_orderid] = strategy
            self.strategy_orderid_map[strategy.strategy_name].add(vt_orderid)
            # 重放抢在注册之前到达的订单 / 成交事件
            # （瞬时成交的网关会在 send_order 内部就推送事件）。
            self._replay_pending_events(vt_orderid)
            vt_orderids.append(vt_orderid)
        if not vt_orderids and reqs == []:
            # 开平转换没有产生任何委托（例如可平仓量已耗尽）：
            # 显式提示而不是静默吞掉。
            self.write_strategy_log(
                strategy,
                f"order dropped by offset conversion (no closeable "
                f"volume?): {req.vt_symbol} {direction.value} "
                f"{offset.value} x{volume}",
                level=logging.WARNING,
            )
        return vt_orderids

    def cancel_order(self, strategy: StrategyTemplate, vt_orderid: str) -> None:
        if vt_orderid.startswith("STOP."):
            stop_order = self.stop_orders.pop(vt_orderid, None)
            if stop_order:
                stop_order.status = StopOrderStatus.CANCELLED
                self._call(strategy, strategy.on_stop_order, stop_order)
            return

        order = self.main_engine.oms.get_order(vt_orderid)
        if not order:
            return
        gateway_name = order.gateway_name
        self.main_engine.cancel_order(order.create_cancel_request(), gateway_name)

    def cancel_all(self, strategy: StrategyTemplate) -> None:
        for vt_orderid in list(
            self.strategy_orderid_map[strategy.strategy_name]
        ):
            self.cancel_order(strategy, vt_orderid)
        for stop_orderid, stop_order in list(self.stop_orders.items()):
            if stop_order.strategy_name == strategy.strategy_name:
                self.cancel_order(strategy, stop_orderid)

    # ------------------------------------------------------------------
    # 数据接口
    # ------------------------------------------------------------------

    def load_bar(
        self,
        vt_symbol: str,
        days: int,
        interval: Interval,
        callback,  # noqa: ANN001
    ) -> None:
        """从数据库加载历史 K线；失败时回退到网关查询。"""
        symbol, exchange = extract_vt_symbol(vt_symbol)
        end = now_cn()
        start = end - timedelta(days=days)

        data_engine = self.main_engine.get_engine("data")
        bars = []
        if data_engine is not None:
            bars = data_engine.load_bars(symbol, exchange, interval, start, end)  # type: ignore[attr-defined]

        if not bars:
            contract = self.main_engine.oms.get_contract(vt_symbol)
            if contract and contract.history_data:
                req = HistoryRequest(
                    symbol=symbol,
                    exchange=exchange,
                    start=start,
                    end=end,
                    interval=interval,
                )
                bars = self.main_engine.query_history(req, contract.gateway_name)
                if bars and data_engine is not None:
                    data_engine.save_bars(bars)  # type: ignore[attr-defined]

        for bar in bars:
            callback(bar)

    # ------------------------------------------------------------------
    # 生命周期管理（REST 线程）
    # ------------------------------------------------------------------

    def add_strategy(
        self,
        class_name: str,
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ) -> StrategyTemplate:
        strategy_class = self.classes.get(class_name)
        if not strategy_class:
            raise ValueError(f"strategy class not found: {class_name}")
        if strategy_name in self.strategies:
            raise KeyError(f"strategy name already exists: {strategy_name}")
        extract_vt_symbol(vt_symbol)  # 校验格式

        strategy = strategy_class(self, strategy_name, vt_symbol, setting)
        self.strategies[strategy_name] = strategy
        self.symbol_strategy_map[vt_symbol].append(strategy)

        self._db_save(strategy, status="STOPPED")
        self.put_strategy_event(strategy)
        self.write_strategy_log(strategy, "strategy added")
        return strategy

    def init_strategy(self, strategy_name: str) -> None:
        self.init_executor.submit(self._init_strategy, strategy_name)

    def _init_strategy(self, strategy_name: str) -> None:
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            return
        if strategy.inited and strategy.trading:
            self.write_strategy_log(strategy, "already initialized and trading")
            return
        # 允许重新初始化（既已停止的可以再次初始化）
        if strategy.inited:
            self.write_strategy_log(strategy, "re-initializing...")
            # 重置策略状态，让 on_init 能重新执行
            strategy.inited = False

        if not self._call(strategy, strategy.on_init):
            return

        # 订阅行情 —— 优先选择真实行情网关而非 SIM
        contract = self.main_engine.oms.get_contract(strategy.vt_symbol)
        if contract:
            symbol, exchange = extract_vt_symbol(strategy.vt_symbol)
            gateway_name = contract.gateway_name
            if gateway_name == "SIM":
                for name, gw in self.main_engine.gateways.items():
                    if name != "SIM" and gw.connected and exchange in gw.exchanges:
                        gateway_name = name
                        break
            self.main_engine.subscribe(
                SubscribeRequest(symbol=symbol, exchange=exchange),
                gateway_name,
            )
        else:
            self.write_strategy_log(
                strategy,
                f"contract not found, market data unavailable: "
                f"{strategy.vt_symbol}",
                level=logging.WARNING,
            )

        strategy.inited = True
        self.put_strategy_event(strategy)
        self.write_strategy_log(strategy, "strategy initialized")

    def start_strategy(self, strategy_name: str) -> None:
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            raise KeyError(f"strategy not found: {strategy_name}")
        if not strategy.inited:
            raise ValueError("strategy not initialized")
        if strategy.trading:
            return

        # 启动前自动对账持仓：检查策略 pos 与网关净持仓是否一致，
        # 不一致时自动同步（仅当该合约未被多个策略共用时）
        self._auto_reconcile_pos(strategy)

        self._call(strategy, strategy.on_start)
        strategy.trading = True
        self._db_update_status(strategy_name, "RUNNING")
        self.put_strategy_event(strategy)
        self.write_strategy_log(strategy, "strategy started")

    def stop_strategy(self, strategy_name: str) -> None:
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            raise KeyError(f"strategy not found: {strategy_name}")
        if not strategy.trading:
            return

        self._call(strategy, strategy.on_stop)
        strategy.trading = False
        self.cancel_all(strategy)
        self._db_update_status(strategy_name, "STOPPED")
        self.put_strategy_event(strategy)
        self.write_strategy_log(strategy, "strategy stopped")

    def edit_strategy(self, strategy_name: str, setting: dict) -> None:
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            raise KeyError(f"strategy not found: {strategy_name}")
        strategy.update_setting(setting)
        self._db_save(strategy, status="RUNNING" if strategy.trading else "STOPPED")
        self.put_strategy_event(strategy)

    def remove_strategy(self, strategy_name: str) -> None:
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            raise KeyError(f"strategy not found: {strategy_name}")
        if strategy.trading:
            raise ValueError("stop the strategy before removing it")

        self.strategies.pop(strategy_name)
        self.symbol_strategy_map[strategy.vt_symbol].remove(strategy)
        self.strategy_orderid_map.pop(strategy_name, None)
        for vt_orderid, mapped in list(self.orderid_strategy_map.items()):
            if mapped is strategy:
                self.orderid_strategy_map.pop(vt_orderid)
        self._db_delete(strategy_name)
        self.write_log(f"strategy removed: {strategy_name}")

    def load_strategies_from_db(self) -> None:
        """启动时从数据库恢复策略实例（一律为 STOPPED 状态）。"""
        try:
            from ...db.models import StrategyModel
            from ...db.session import create_session

            session = create_session()
            try:
                rows = session.query(StrategyModel).all()
            finally:
                session.close()
        except Exception:
            self.write_log(
                f"failed to load strategies from db:\n{traceback.format_exc()}",
                level=logging.WARNING,
            )
            return

        self._auto_start_names: set[str] = set()
        for row in rows:
            if row.name in self.strategies:
                continue
            if row.class_name not in self.classes:
                self.write_log(
                    f"skip strategy {row.name}: class {row.class_name} "
                    f"not available",
                    level=logging.WARNING,
                )
                continue
            try:
                vt_symbols = json.loads(row.vt_symbols)
                vt_symbol = vt_symbols[0] if vt_symbols else ""
                setting = json.loads(row.setting)
                strategy_class = self.classes[row.class_name]
                strategy = strategy_class(self, row.name, vt_symbol, setting)

                # 恢复持久化的运行时状态 —— 最重要的是 pos，
                # 让昨日的持仓在重启后仍可交易。指标值不会被
                # 盲目恢复：只恢复 pos 以及类中声明的变量。
                try:
                    saved = json.loads(row.variables or "{}")
                except Exception:
                    saved = {}
                if "pos" in saved:
                    try:
                        strategy.pos = float(saved["pos"])
                    except (TypeError, ValueError):
                        pass
                for var_name in strategy.variables:
                    if var_name in saved:
                        try:
                            setattr(strategy, var_name, saved[var_name])
                        except Exception:
                            pass
                if strategy.pos:
                    self.write_log(
                        f"[{row.name}] restored pos={strategy.pos} "
                        f"from previous session"
                    )

                # 恢复自动启动标记
                strategy.auto_start = bool(getattr(row, "auto_start", False))

                self.strategies[row.name] = strategy
                self.symbol_strategy_map[vt_symbol].append(strategy)
                self.put_strategy_event(strategy)

                # 记录需要自动启动的策略
                if getattr(row, "auto_start", False):
                    self._auto_start_names.add(row.name)
            except Exception:
                self.write_log(
                    f"failed to restore strategy {row.name}:\n"
                    f"{traceback.format_exc()}",
                    level=logging.WARNING,
                )
        if rows:
            self.write_log(f"restored {len(self.strategies)} strategies from db")
            if self._auto_start_names:
                self.write_log(
                    f"auto-start strategies: {self._auto_start_names}",
                )

    def auto_start_strategies(self) -> None:
        """启动标记为自动启动的策略（在网关连接后调用）。

        注意：此方法在后台线程中运行，因此可以同步阻塞等初始化完成。
        """
        import time as _time
        for name in list(getattr(self, "_auto_start_names", set())):
            strategy = self.strategies.get(name)
            if not strategy:
                continue
            try:
                # 等待合约可用（CTP 登录 + 合约查询需要时间），最多等 60 秒
                contract = None
                deadline = _time.time() + 60
                while _time.time() < deadline:
                    contract = self.main_engine.oms.get_contract(
                        strategy.vt_symbol
                    )
                    if contract:
                        break
                    _time.sleep(1)
                if contract is None:
                    self.write_strategy_log(
                        strategy,
                        f"auto-start skipped: contract not found after 60s for "
                        f"{strategy.vt_symbol}",
                        level=logging.WARNING,
                    )
                    continue

                # 同步初始化（阻塞等待 on_init 完成），再启动
                if not strategy.inited:
                    self._init_strategy(name)
                if not strategy.inited:
                    self.write_strategy_log(
                        strategy,
                        "auto-start skipped: initialization failed",
                        level=logging.WARNING,
                    )
                    continue
                self.start_strategy(name)
                self.write_strategy_log(
                    strategy, f"auto-started (auto_start=true)"
                )
            except Exception:
                self.write_log(
                    f"failed to auto-start strategy {name}:\n"
                    f"{traceback.format_exc()}",
                    level=logging.WARNING,
                )

    # ------------------------------------------------------------------
    # 数据库辅助方法
    # ------------------------------------------------------------------

    def _db_save(self, strategy: StrategyTemplate, status: str) -> None:
        try:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            from ...db.models import StrategyModel
            from ...db.session import create_session, get_engine

            row = {
                "name": strategy.strategy_name,
                "class_name": type(strategy).__name__,
                "vt_symbols": json.dumps([strategy.vt_symbol]),
                "setting": json.dumps(strategy.get_parameters()),
                "status": status,
                "auto_start": getattr(strategy, "auto_start", False),
            }
            session = create_session()
            try:
                if get_engine().dialect.name == "sqlite":
                    stmt = sqlite_insert(StrategyModel).values(**row)
                else:
                    from sqlalchemy.dialects.postgresql import (
                        insert as pg_insert,
                    )

                    stmt = pg_insert(StrategyModel).values(**row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["name"],
                    set_={k: v for k, v in row.items() if k != "name"},
                )
                session.execute(stmt)
                session.commit()
            finally:
                session.close()
        except Exception:
            self.write_log(
                f"failed to persist strategy:\n{traceback.format_exc()}",
                level=logging.WARNING,
            )

    def _auto_reconcile_pos(self, strategy: StrategyTemplate) -> None:
        """启动前检查策略 pos 与网关净持仓是否一致，不一致时自动同步。

        仅当该合约未被多个策略共用时执行自动同步，否则跳过并记录日志。
        """
        peers = [
            s
            for s in self.strategies.values()
            if s.vt_symbol == strategy.vt_symbol
        ]
        if len(peers) > 1:
            self.write_strategy_log(
                strategy,
                f"启动前自动对账跳过：合约 {strategy.vt_symbol} 被多个策略共用，"
                f"请手动同步持仓",
                level=logging.WARNING,
            )
            return

        net = 0.0
        for p in self.main_engine.oms.get_all_positions():
            if p.vt_symbol != strategy.vt_symbol:
                continue
            if p.direction == Direction.SHORT:
                net -= p.volume
            else:
                net += p.volume

        if strategy.pos == net:
            return  # 一致，无需同步

        old_pos = strategy.pos
        strategy.pos = net
        self._db_save_variables(strategy)
        self.write_strategy_log(
            strategy,
            f"启动前自动对账：pos {old_pos} → {net}（网关净持仓）",
        )

    def _db_update_status(self, strategy_name: str, status: str) -> None:
        try:
            from ...db.models import StrategyModel
            from ...db.session import create_session

            session = create_session()
            try:
                session.query(StrategyModel).filter_by(
                    name=strategy_name
                ).update({"status": status})
                session.commit()
            finally:
                session.close()
        except Exception:
            pass

    def set_auto_start(self, strategy_name: str, enabled: bool) -> bool:
        """设置策略是否在启动时自动初始化并启动。"""
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            raise KeyError(f"strategy not found: {strategy_name}")
        strategy.auto_start = enabled
        try:
            from ...db.models import StrategyModel
            from ...db.session import create_session

            session = create_session()
            try:
                session.query(StrategyModel).filter_by(
                    name=strategy_name
                ).update({"auto_start": enabled})
                session.commit()
            finally:
                session.close()
            self.write_strategy_log(
                strategy, f"auto_start set to {enabled}"
            )
        except Exception:
            self.write_log(
                f"failed to persist auto_start for {strategy_name}:\n"
                f"{traceback.format_exc()}",
                level=logging.WARNING,
            )
        self.put_strategy_event(strategy)
        return enabled

    def _db_save_variables(self, strategy: StrategyTemplate) -> None:
        """每笔成交后持久化运行时变量（pos 等）。

        在事件分发线程中执行；每笔成交一次小 UPDATE 的开销很低，
        并使持仓具备重启安全性（vn.py 把同样的状态保存在
        cta_strategy_data.json 中）。
        """
        try:
            from ...db.models import StrategyModel
            from ...db.session import create_session

            variables = strategy.get_variables()
            # inited / trading 是生命周期标志位，不属于可交易状态
            variables.pop("inited", None)
            variables.pop("trading", None)

            session = create_session()
            try:
                session.query(StrategyModel).filter_by(
                    name=strategy.strategy_name
                ).update({"variables": json.dumps(variables, default=str)})
                session.commit()
            finally:
                session.close()
        except Exception:
            self.write_log(
                f"failed to persist strategy variables:\n"
                f"{traceback.format_exc()}",
                level=logging.WARNING,
            )

    def _db_delete(self, strategy_name: str) -> None:
        try:
            from ...db.models import StrategyModel
            from ...db.session import create_session

            session = create_session()
            try:
                session.query(StrategyModel).filter_by(
                    name=strategy_name
                ).delete()
                session.commit()
            finally:
                session.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 持仓对账（策略 pos 与网关持仓）
    # ------------------------------------------------------------------

    def reconcile_positions(self) -> list[dict]:
        """比较每个策略的 pos 与网关净持仓。

        某个 vt_symbol 的网关净持仓 = OMS 持仓缓存中的多头数量
        减去空头数量（由真实网关的持仓推送填充 —— CTP 查询轮询 /
        SIM 快照）。

        出现不一致通常意味着持仓在策略之外被改动：策略停止期间
        的手动交易、崩溃导致丢失的成交，或另一策略 / 账户在交易
        同一合约。注意：当多个策略共用一个 vt_symbol 时，网关
        净持仓与它们 pos 的总和比较，且相关行会被标记
        ``shared=True`` —— 这些行拒绝自动同步。
        """
        # 按合约汇总策略 pos，以检测共用情况
        pos_sum: dict[str, float] = {}
        count_per_symbol: dict[str, int] = {}
        for s in self.strategies.values():
            pos_sum[s.vt_symbol] = pos_sum.get(s.vt_symbol, 0.0) + s.pos
            count_per_symbol[s.vt_symbol] = (
                count_per_symbol.get(s.vt_symbol, 0) + 1
            )

        rows: list[dict] = []
        for strategy in self.strategies.values():
            vt_symbol = strategy.vt_symbol
            net = 0.0
            found_position = False
            for p in self.main_engine.oms.get_all_positions():
                if p.vt_symbol != vt_symbol:
                    continue
                found_position = True
                if p.direction == Direction.SHORT:
                    net -= p.volume
                else:
                    net += p.volume

            shared = count_per_symbol[vt_symbol] > 1
            compare = pos_sum[vt_symbol] if shared else strategy.pos
            rows.append(
                {
                    "strategy_name": strategy.strategy_name,
                    "vt_symbol": vt_symbol,
                    "strategy_pos": strategy.pos,
                    "gateway_net_pos": net,
                    "diff": compare - net,
                    "matched": abs(compare - net) < 1e-9,
                    "shared": shared,
                    "gateway_has_position": found_position,
                    "trading": strategy.trading,
                }
            )
        return rows

    def sync_strategy_pos(self, strategy_name: str, pos: float | None = None) -> dict:
        """用网关净持仓覆盖策略的 pos，或手动指定 pos。

        当合约被多个策略共用时，引擎无法自动分配净持仓，此时
        需要传入 ``pos`` 手动指定每个策略应持有的数量。
        """
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            raise KeyError(f"strategy not found: {strategy_name}")
        if strategy.trading:
            raise ValueError("请先停止策略再同步持仓")

        if pos is not None:
            # 手动指定：跳过 peer 检查，直接设置
            old_pos = strategy.pos
            strategy.pos = pos
            self._db_save_variables(strategy)
            self.put_strategy_event(strategy)
            self.write_strategy_log(
                strategy,
                f"pos manually set: {old_pos} -> {pos}",
            )
            return {
                "strategy_name": strategy_name,
                "old_pos": old_pos,
                "new_pos": pos,
                "mode": "manual",
            }

        peers = [
            s
            for s in self.strategies.values()
            if s.vt_symbol == strategy.vt_symbol
        ]
        if len(peers) > 1:
            raise ValueError(
                f"合约 {strategy.vt_symbol} 被 {len(peers)} 个策略共用，"
                f"无法自动分配净持仓，请手动处理。"
                f"例如：POST /api/strategies/{strategy_name}/sync-pos "
                f'{{"pos": 5}} 表示此策略持有 5 手'
            )

        net = 0.0
        for p in self.main_engine.oms.get_all_positions():
            if p.vt_symbol != strategy.vt_symbol:
                continue
            if p.direction == Direction.SHORT:
                net -= p.volume
            else:
                net += p.volume

        old_pos = strategy.pos
        strategy.pos = net
        self._db_save_variables(strategy)
        self.put_strategy_event(strategy)
        self.write_strategy_log(
            strategy,
            f"pos reconciled: {old_pos} -> {net} (gateway net position)",
        )
        return {
            "strategy_name": strategy_name,
            "old_pos": old_pos,
            "new_pos": net,
            "mode": "auto",
        }

    # ------------------------------------------------------------------
    # 信息查询 / 事件
    # ------------------------------------------------------------------

    def get_all_strategy_data(self) -> list[dict]:
        return [s.get_data() for s in self.strategies.values()]

    def get_strategy_class_info(self) -> list[dict]:
        return [
            {
                "class_name": name,
                "display_name": cls.display_name or name,
                "author": cls.author,
                "description": cls.description or (cls.__doc__ or "").strip(),
                "parameters": cls.get_class_parameters(),
                "param_descriptions": cls.param_descriptions,
                "variable_descriptions": cls.variable_descriptions,
                "variables": list(cls.variables),
            }
            for name, cls in sorted(self.classes.items())
        ]

    def put_strategy_event(self, strategy: StrategyTemplate) -> None:
        self.event_engine.put(Event(EVENT_STRATEGY, strategy.get_data()))

    def write_strategy_log(
        self,
        strategy: StrategyTemplate,
        msg: str,
        level: int = logging.INFO,
    ) -> None:
        self.event_engine.put(
            Event(
                EVENT_STRATEGY_LOG,
                {
                    "strategy_name": strategy.strategy_name,
                    "msg": msg,
                    "time": now_cn().isoformat(),
                },
            )
        )
        self.main_engine.write_log(
            f"[{strategy.strategy_name}] {msg}",
            source="StrategyEngine",
            level=level,
        )

    def write_log(self, msg: str, level: int = logging.INFO) -> None:
        self.main_engine.write_log(msg, source="StrategyEngine", level=level)

    def close(self) -> None:
        for strategy in self.strategies.values():
            if strategy.trading:
                self.stop_strategy(strategy.strategy_name)
        self.init_executor.shutdown(wait=False)
