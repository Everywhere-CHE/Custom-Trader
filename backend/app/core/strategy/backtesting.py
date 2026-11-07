"""CTA 回测引擎（同步 K线 重放）。

实现与 StrategyEngine 相同的鸭子类型接口（即 StrategyTemplate
所期望的接口），因此任何策略无需修改即可同时运行于实盘和
回测模式。参考 vnpy_ctastrategy 的 BacktestingEngine 设计：

- 基于 K线 的撮合：K线 价格区间触及委托价时限价单成交；
  停止单在区间突破时触发并立即成交；
- 逐日盯市盈亏（DailyResult）；
- 用 numpy 计算统计指标（年化收益、最大回撤、夏普比率、
  胜率、盈亏比）。
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np

from ..constant import Direction, Exchange, Interval, Offset, OrderType, Status
from ..object import BarData, OrderData, TradeData
from ..utility import extract_vt_symbol, round_to
from .template import StopOrder, StopOrderStatus, StrategyTemplate


class DailyResult:
    """回测单一合约的逐日盯市结果。"""

    def __init__(self, result_date: date, close_price: float) -> None:
        self.date: date = result_date
        self.close_price: float = close_price
        self.pre_close: float = 0.0

        self.trades: list[TradeData] = []
        self.trade_count: int = 0

        self.start_pos: float = 0.0
        self.end_pos: float = 0.0

        self.turnover: float = 0.0
        self.commission: float = 0.0
        self.slippage: float = 0.0

        self.trading_pnl: float = 0.0
        self.holding_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.net_pnl: float = 0.0

    def add_trade(self, trade: TradeData) -> None:
        self.trades.append(trade)

    def calculate_pnl(
        self,
        pre_close: float,
        start_pos: float,
        size: float,
        rate: float,
        slippage: float,
    ) -> None:
        self.pre_close = pre_close or self.close_price  # 首日无前收盘价时，持有盈亏为 0
        self.start_pos = start_pos
        self.end_pos = start_pos

        # 重置累加器，使 calculate_pnl 具备幂等性（它会被
        # calculate_statistics 和 get_daily_results 分别调用）。
        self.turnover = 0.0
        self.commission = 0.0
        self.slippage = 0.0
        self.trading_pnl = 0.0

        self.holding_pnl = start_pos * (self.close_price - self.pre_close) * size

        for trade in self.trades:
            if trade.direction == Direction.LONG:
                pos_change = trade.volume
            else:
                pos_change = -trade.volume
            self.end_pos += pos_change

            turnover = trade.volume * size * trade.price
            self.trading_pnl += (
                pos_change * (self.close_price - trade.price) * size
            )
            self.slippage += trade.volume * size * slippage
            self.turnover += turnover
            self.commission += turnover * rate

        self.trade_count = len(self.trades)
        self.total_pnl = self.trading_pnl + self.holding_pnl
        self.net_pnl = self.total_pnl - self.commission - self.slippage

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "close_price": self.close_price,
            "pre_close": self.pre_close,
            "trade_count": self.trade_count,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "turnover": self.turnover,
            "commission": self.commission,
            "slippage": self.slippage,
            "trading_pnl": self.trading_pnl,
            "holding_pnl": self.holding_pnl,
            "total_pnl": self.total_pnl,
            "net_pnl": self.net_pnl,
        }


class BacktestingEngine:
    """单合约 CTA 回测引擎。"""

    gateway_name: str = "BACKTESTING"

    def __init__(self) -> None:
        self.vt_symbol: str = ""
        self.symbol: str = ""
        self.exchange: Exchange = Exchange.LOCAL
        self.interval: Interval = Interval.MINUTE
        self.start: datetime = datetime(2000, 1, 1)
        self.end: datetime | None = None
        self.rate: float = 0.0
        self.slippage: float = 0.0
        self.size: float = 1.0
        self.pricetick: float = 0.01
        self.capital: float = 1_000_000
        self.risk_free: float = 0.0
        self.annual_days: int = 240

        self.strategy_class: type[StrategyTemplate] | None = None
        self.strategy: StrategyTemplate | None = None

        self.history_data: list[BarData] = []
        self.bar: BarData | None = None
        self.datetime: datetime | None = None

        self.limit_order_count: int = 0
        self.active_limit_orders: dict[str, OrderData] = {}
        self.limit_orders: dict[str, OrderData] = {}

        self.stop_order_count: int = 0
        self.active_stop_orders: dict[str, StopOrder] = {}
        self.stop_orders: dict[str, StopOrder] = {}

        self.trade_count: int = 0
        self.trades: dict[str, TradeData] = {}

        self.daily_results: dict[date, DailyResult] = {}
        self.logs: list[str] = []

        # 预热阶段标志：在 on_init 的 load_bar 重放期间使用
        self._warmup_callback = None
        self._busted: bool = False

    # ------------------------------------------------------------------
    # 参数设置
    # ------------------------------------------------------------------

    def set_parameters(
        self,
        vt_symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime | None = None,
        rate: float = 0.0,
        slippage: float = 0.0,
        size: float = 1.0,
        pricetick: float = 0.01,
        capital: float = 1_000_000,
        risk_free: float = 0.0,
        annual_days: int = 240,
    ) -> None:
        self.vt_symbol = vt_symbol
        self.symbol, self.exchange = extract_vt_symbol(vt_symbol)
        self.interval = interval
        self.start = start
        self.end = end
        self.rate = rate
        self.slippage = slippage
        self.size = size
        self.pricetick = pricetick
        self.capital = capital
        self.risk_free = risk_free
        self.annual_days = annual_days

    def add_strategy(
        self, strategy_class: type[StrategyTemplate], setting: dict
    ) -> None:
        self.strategy_class = strategy_class
        self.strategy = strategy_class(
            self, strategy_class.__name__, self.vt_symbol, setting
        )

    def close_at_end(self) -> None:
        """回测窗口结束时强平未平仓位（按最后一根 K 线收盘价成交）。

        用于优化器按段评估：保证每段统计自洽（段内盈亏全部落袋），
        避免浮仓盈亏被静默丢弃造成段间不可比。
        """
        assert self.strategy is not None
        pos = self.strategy.pos
        if not pos or self.bar is None:
            return
        # 清掉未成交挂单，避免强平后再被旧单干扰
        self.active_limit_orders.clear()
        self.active_stop_orders.clear()

        self.limit_order_count += 1
        orderid = str(self.limit_order_count)
        direction = Direction.SHORT if pos > 0 else Direction.LONG
        volume = abs(pos)
        # 收盘价按方向加滑点（对不利方向）
        price = self.bar.close_price
        if direction == Direction.LONG:
            price = price + self.slippage
        else:
            price = price - self.slippage
        if self.pricetick:
            price = round_to(price, self.pricetick)
        self._fill(direction, Offset.CLOSE, price, volume, orderid)

    def load_data(self, data_engine) -> None:  # noqa: ANN001
        """从数据库加载 K线（包含回测开始前的预热余量）。
        若请求的周期没有数据，则回退到最接近的可用周期。"""
        warmup_start = self.start - timedelta(days=30)
        self.history_data = data_engine.load_bars(
            self.symbol, self.exchange, self.interval, warmup_start, self.end
        )
        if self.history_data:
            return
        # 回退：尝试其他周期
        from ..constant import Interval
        for fallback in [Interval.HOUR, Interval.DAILY, Interval.WEEKLY]:
            if fallback == self.interval:
                continue
            self.history_data = data_engine.load_bars(
                self.symbol, self.exchange, fallback, warmup_start, self.end
            )
            if self.history_data:
                self.write_log(
                    f"WARNING: 没有 {self.interval.value} 数据，"
                    f"回退到 {fallback.value} 进行回测"
                )
                self.interval = fallback
                break

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    def run_backtesting(self) -> None:
        assert self.strategy is not None
        if not self.history_data:
            raise ValueError("no history data loaded")

        self._busted = False

        strategy = self.strategy

        # 预热：self.start 之前的 K线 只喂给 load_bar 的回调
        self._pre_start_bars = [
            b for b in self.history_data
            if b.datetime is not None and b.datetime < self.start
        ]
        replay_bars = [
            b for b in self.history_data
            if b.datetime is not None and b.datetime >= self.start
            and (self.end is None or b.datetime <= self.end)
        ]
        if not replay_bars:
            raise ValueError("no bars within the backtest window")

        strategy.on_init()
        strategy.inited = True
        self.write_log("strategy initialized")

        strategy.on_start()
        strategy.trading = True
        self.write_log("backtest replay started")

        for bar in replay_bars:
            self.new_bar(bar)
            if self._busted:
                self.write_log("backtest stopped early due to account busted")
                break

        strategy.on_stop()
        strategy.trading = False
        self.write_log("backtest replay finished")

    def new_bar(self, bar: BarData) -> None:
        self.bar = bar
        self.datetime = bar.datetime

        self.cross_limit_order()
        self.cross_stop_order()
        assert self.strategy is not None
        self.strategy.on_bar(bar)

        self.update_daily_close(bar.close_price)

    # ------------------------------------------------------------------
    # 撮合
    # ------------------------------------------------------------------

    def cross_limit_order(self) -> None:
        """用当前 K线 的价格区间撮合限价单。"""
        assert self.bar is not None and self.strategy is not None
        long_cross_price = self.bar.low_price
        short_cross_price = self.bar.high_price
        long_best_price = self.bar.open_price
        short_best_price = self.bar.open_price

        for order in list(self.active_limit_orders.values()):
            if order.status == Status.SUBMITTING:
                order.status = Status.NOTTRADED
                self.strategy.on_order(order)

            long_cross = (
                order.direction == Direction.LONG
                and order.price >= long_cross_price
            )
            short_cross = (
                order.direction == Direction.SHORT
                and order.price <= short_cross_price
            )
            if not long_cross and not short_cross:
                continue

            order.traded = order.volume
            order.status = Status.ALLTRADED
            self.active_limit_orders.pop(order.vt_orderid)
            self.strategy.on_order(order)

            if long_cross:
                trade_price = min(order.price, long_best_price)
            else:
                trade_price = max(order.price, short_best_price)

            self._fill(order.direction, order.offset, trade_price,
                       order.volume, order.orderid)

    def cross_stop_order(self) -> None:
        """K线 区间突破时触发停止单；立即成交。"""
        assert self.bar is not None and self.strategy is not None
        for stop_order in list(self.active_stop_orders.values()):
            long_triggered = (
                stop_order.direction == Direction.LONG
                and self.bar.high_price >= stop_order.price
            )
            short_triggered = (
                stop_order.direction == Direction.SHORT
                and self.bar.low_price <= stop_order.price
            )
            if not long_triggered and not short_triggered:
                continue

            self.active_stop_orders.pop(stop_order.stop_orderid)

            if long_triggered:
                trade_price = max(stop_order.price, self.bar.open_price)
            else:
                trade_price = min(stop_order.price, self.bar.open_price)

            # 合成一笔已成交的限价单用于记账
            self.limit_order_count += 1
            orderid = str(self.limit_order_count)
            order = OrderData(
                gateway_name=self.gateway_name,
                symbol=self.symbol,
                exchange=self.exchange,
                orderid=orderid,
                direction=stop_order.direction,
                offset=stop_order.offset,
                price=stop_order.price,
                volume=stop_order.volume,
                traded=stop_order.volume,
                status=Status.ALLTRADED,
                datetime=self.datetime,
            )
            self.limit_orders[order.vt_orderid] = order
            stop_order.vt_orderids.append(order.vt_orderid)
            stop_order.status = StopOrderStatus.TRIGGERED
            self.strategy.on_stop_order(stop_order)
            self.strategy.on_order(order)

            self._fill(stop_order.direction, stop_order.offset, trade_price,
                       stop_order.volume, orderid)

    def _fill(
        self,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        orderid: str,
    ) -> None:
        """记录成交，更新策略 pos，调用 on_trade。"""
        assert self.strategy is not None
        self.trade_count += 1
        trade = TradeData(
            gateway_name=self.gateway_name,
            symbol=self.symbol,
            exchange=self.exchange,
            orderid=orderid,
            tradeid=str(self.trade_count),
            direction=direction,
            offset=offset,
            price=price,
            volume=volume,
            datetime=self.datetime,
        )
        self.trades[trade.vt_tradeid] = trade

        if direction == Direction.LONG:
            self.strategy.pos += volume
        else:
            self.strategy.pos -= volume
        self.strategy.on_trade(trade)

        if self.datetime is not None:
            daily = self._get_daily_result(self.datetime.date())
            daily.add_trade(trade)

    # ------------------------------------------------------------------
    # 策略接口（与 StrategyEngine 相同的鸭子类型）
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
        if self._busted:
            return []
        price = round_to(price, self.pricetick)
        if stop:
            return [self._send_stop_order(direction, offset, price, volume)]
        return [self._send_limit_order(direction, offset, price, volume)]

    def _send_limit_order(
        self, direction: Direction, offset: Offset, price: float, volume: float
    ) -> str:
        self.limit_order_count += 1
        order = OrderData(
            gateway_name=self.gateway_name,
            symbol=self.symbol,
            exchange=self.exchange,
            orderid=str(self.limit_order_count),
            type=OrderType.LIMIT,
            direction=direction,
            offset=offset,
            price=price,
            volume=volume,
            status=Status.SUBMITTING,
            datetime=self.datetime,
        )
        self.active_limit_orders[order.vt_orderid] = order
        self.limit_orders[order.vt_orderid] = order
        return order.vt_orderid

    def _send_stop_order(
        self, direction: Direction, offset: Offset, price: float, volume: float
    ) -> str:
        assert self.strategy is not None
        self.stop_order_count += 1
        stop_order = StopOrder(
            vt_symbol=self.vt_symbol,
            direction=direction,
            offset=offset,
            price=price,
            volume=volume,
            stop_orderid=f"STOP.{self.stop_order_count}",
            strategy_name=self.strategy.strategy_name,
            datetime=self.datetime or datetime.now(),
        )
        self.active_stop_orders[stop_order.stop_orderid] = stop_order
        self.stop_orders[stop_order.stop_orderid] = stop_order
        self.strategy.on_stop_order(stop_order)
        return stop_order.stop_orderid

    def cancel_order(self, strategy: StrategyTemplate, vt_orderid: str) -> None:
        if vt_orderid.startswith("STOP."):
            stop_order = self.active_stop_orders.pop(vt_orderid, None)
            if stop_order:
                stop_order.status = StopOrderStatus.CANCELLED
                strategy.on_stop_order(stop_order)
        else:
            order = self.active_limit_orders.pop(vt_orderid, None)
            if order:
                order.status = Status.CANCELLED
                strategy.on_order(order)

    def cancel_all(self, strategy: StrategyTemplate) -> None:
        for vt_orderid in list(self.active_limit_orders):
            self.cancel_order(strategy, vt_orderid)
        for stop_orderid in list(self.active_stop_orders):
            self.cancel_order(strategy, stop_orderid)

    def load_bar(
        self,
        vt_symbol: str,
        days: int,
        interval: Interval,
        callback,  # noqa: ANN001
    ) -> None:
        """把预热 K线（回测开始之前的）重放给回调函数。"""
        for bar in getattr(self, "_pre_start_bars", []):
            callback(bar)

    def write_strategy_log(self, strategy: StrategyTemplate, msg: str) -> None:
        self.write_log(f"[{strategy.strategy_name}] {msg}")

    def write_log(self, msg: str) -> None:
        prefix = self.datetime.isoformat() if self.datetime else ""
        self.logs.append(f"{prefix}\t{msg}")

    def put_strategy_event(self, strategy: StrategyTemplate) -> None:
        """回测中为空操作。"""

    # ------------------------------------------------------------------
    # 结果统计
    # ------------------------------------------------------------------

    def _get_daily_result(self, result_date: date) -> DailyResult:
        daily = self.daily_results.get(result_date)
        if not daily:
            close = self.bar.close_price if self.bar else 0
            daily = DailyResult(result_date, close)
            self.daily_results[result_date] = daily
        return daily

    def update_daily_close(self, price: float) -> None:
        assert self.datetime is not None
        daily = self._get_daily_result(self.datetime.date())
        daily.close_price = price

    def calculate_result(self) -> list[dict]:
        """按日计算盈亏链。"""
        if not self.daily_results:
            return []

        pre_close = 0.0
        start_pos = 0.0
        for daily in self.daily_results.values():  # 按插入顺序遍历
            daily.calculate_pnl(
                pre_close, start_pos, self.size, self.rate, self.slippage
            )
            pre_close = daily.close_price
            start_pos = daily.end_pos

        return [d.to_dict() for d in self.daily_results.values()]

    def calculate_statistics(self) -> dict:
        """用 numpy 计算汇总统计指标。"""
        daily_dicts = self.calculate_result()
        if not daily_dicts:
            return {}

        dates = [d["date"] for d in daily_dicts]
        net_pnls = np.array([d["net_pnl"] for d in daily_dicts])
        balance = self.capital + np.cumsum(net_pnls)

        # 爆仓检测
        if (balance <= 0).any():
            self._busted = True
            self.write_log("ACCOUNT BUSTED: balance <= 0, trading stopped")
            # 截断 balance 为 0，避免后续统计失真
            balance = np.maximum(balance, 0)

        pre_balance = np.concatenate(([self.capital], balance[:-1]))
        returns = np.where(pre_balance != 0, net_pnls / pre_balance, 0.0)

        highlevel = np.maximum.accumulate(balance)
        drawdown = balance - highlevel
        ddpercent = np.where(highlevel != 0, drawdown / highlevel * 100, 0.0)

        # 最大回撤持续天数
        max_dd_end = int(np.argmin(drawdown))
        if drawdown[max_dd_end] < 0:
            max_dd_start = int(np.argmax(balance[: max_dd_end + 1]))
            max_dd_duration = max_dd_end - max_dd_start
        else:
            max_dd_duration = 0

        total_days = len(daily_dicts)
        profit_days = int((net_pnls > 0).sum())
        loss_days = int((net_pnls < 0).sum())

        end_balance = float(balance[-1])
        total_return = (end_balance / self.capital - 1) * 100
        annual_return = total_return / total_days * self.annual_days
        daily_return = float(returns.mean() * 100)
        return_std = float(returns.std() * 100)

        if return_std:
            # daily_return / return_std 是百分数；先把年化无风险
            # 利率（小数）换算成日度百分数，使单位一致后再做
            # 年化处理。
            daily_risk_free = self.risk_free / self.annual_days * 100
            sharpe = (
                (daily_return - daily_risk_free)
                / return_std
                * np.sqrt(self.annual_days)
            )
        else:
            sharpe = 0.0

        max_ddpercent = float(ddpercent.min())
        return_drawdown_ratio = (
            -total_return / max_ddpercent if max_ddpercent else 0.0
        )

        win_rate, profit_factor = self._win_stats()

        stats = {
            "start_date": dates[0],
            "end_date": dates[-1],
            "total_days": total_days,
            "profit_days": profit_days,
            "loss_days": loss_days,
            "capital": self.capital,
            "end_balance": end_balance,
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": float(drawdown.min()),
            "max_ddpercent": max_ddpercent,
            "max_drawdown_duration": max_dd_duration,
            "total_net_pnl": float(net_pnls.sum()),
            "total_commission": float(
                sum(d["commission"] for d in daily_dicts)
            ),
            "total_slippage": float(sum(d["slippage"] for d in daily_dicts)),
            "total_turnover": float(sum(d["turnover"] for d in daily_dicts)),
            "total_trade_count": self.trade_count,
            "daily_return": daily_return,
            "return_std": return_std,
            "sharpe_ratio": float(sharpe),
            "return_drawdown_ratio": float(return_drawdown_ratio),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
        }
        # 清理 NaN / inf
        for key, value in stats.items():
            if isinstance(value, float) and not np.isfinite(value):
                stats[key] = 0.0
        return stats

    def _win_stats(self) -> tuple[float, float]:
        """通过 FIFO 开平配对计算胜率（%）和盈亏比。"""
        long_queue: list[list[float]] = []   # [价格, 数量]
        short_queue: list[list[float]] = []
        round_trips: list[float] = []

        for trade in self.trades.values():
            volume = trade.volume
            if trade.direction == Direction.LONG:
                # 先平掉空头，再开多头
                while volume > 0 and short_queue:
                    entry = short_queue[0]
                    matched = min(volume, entry[1])
                    round_trips.append(
                        (entry[0] - trade.price) * matched * self.size
                    )
                    entry[1] -= matched
                    volume -= matched
                    if entry[1] <= 0:
                        short_queue.pop(0)
                if volume > 0:
                    long_queue.append([trade.price, volume])
            else:
                while volume > 0 and long_queue:
                    entry = long_queue[0]
                    matched = min(volume, entry[1])
                    round_trips.append(
                        (trade.price - entry[0]) * matched * self.size
                    )
                    entry[1] -= matched
                    volume -= matched
                    if entry[1] <= 0:
                        long_queue.pop(0)
                if volume > 0:
                    short_queue.append([trade.price, volume])

        if not round_trips:
            return 0.0, 0.0

        wins = [p for p in round_trips if p > 0]
        losses = [p for p in round_trips if p < 0]
        win_rate = len(wins) / len(round_trips) * 100
        total_win = sum(wins)
        total_loss = abs(sum(losses))
        profit_factor = total_win / total_loss if total_loss else 0.0
        return float(win_rate), float(profit_factor)

    def get_daily_results(self) -> list[dict]:
        return self.calculate_result()

    def get_all_trades(self) -> list[TradeData]:
        return list(self.trades.values())
