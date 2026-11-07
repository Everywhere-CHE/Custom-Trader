"""策略2：均线突破 + 黄金分割回调（事件驱动版）。

将原始自包含回测函数 ``strategy2_ma_break_retrace`` 改写为
``StrategyTemplate`` 子类，在实盘/回测中以事件驱动方式运行。

逻辑要点（与回测版一致）：
- 做多：前期下跌（past_ret <= -trend_return）→ 突破均线（超过
  breakout_pct 幅度）+ 大阳线（收盘涨幅 >= big_candle_atr 倍 ATR 百分比）
  → 进入等待回调状态 → 价格回调至该阳线实体（开盘→收盘）的黄金分割位
  且收盘站稳确认 → 开多。
- 做空：前期上涨 → 跌破均线 + 大阴线 → 反弹至阴线实体黄金分割位
  → 收盘站稳确认 → 开空。
- pending 状态最多等待 max_wait 根日线，超时失效；跌破/突破触发K线
  极值时也失效。

执行口径：回调确认当日以收盘价 ± 缓冲发出限价单。回测中该限价单在
次日 K 线以开盘价撮合，等价于"次日开盘价执行"；实盘订单留至下一交易日
开盘成交。

出场体系（四重）：
1. 固定止损：开仓价 ∓ stop_loss_atr × ATR，本地停止单盘中触发；
2. 止盈：开仓价 ± take_profit_atr × ATR，本地停止单；
3. 移动止损：从持仓最高/最低价回撤 trailing_stop_atr × ATR（收盘价判断）；
4. 时间止损：持仓满 max_hold_bars 根日线 K 线平仓。

黄金分割位基于 K 线实体（开盘→收盘）计算，收盘即确定，无未来函数；
ATR 用"前一日"值。

支持可配置 K 线周期（``bar_interval`` 参数）：
- ``"d"``（默认）：日线，需设置 ``daily_close_time``
- ``"5m"``/``"15m"``/``"30m"``/``"1h"``：分钟/小时线，直接使用实盘 1 分钟数据聚合
- ``"1m"``：一分线，直接逐根处理
"""

import numpy as np

from app.core.constant import Interval
from app.core.object import BarData, TickData
from app.core.strategy.array_manager import ArrayManager
from app.core.strategy.bar_generator import BarGenerator
from app.core.strategy.template import StrategyTemplate

# 周期映射
_BAR_INTERVAL_MAP: dict[str, Interval] = {
    "d": Interval.DAILY, "1h": Interval.HOUR, "1m": Interval.MINUTE,
    "5m": Interval.MINUTE5, "15m": Interval.MINUTE15, "30m": Interval.MINUTE30,
}


class MaBreakRetraceStrategy(StrategyTemplate):
    """均线突破 + 黄金分割回调策略（事件驱动版）。"""

    author = "custom-trader"
    display_name = "均线突破回调"
    description = (
        "均线突破 + 黄金分割回调策略：前期下跌后大阳线突破均线，等待价格回调至"
        "该阳线实体的黄金分割位（0.382/0.5/0.618）且收盘站稳确认后做多；"
        "前期上涨后大阴线跌破均线，反弹至阴线实体黄金分割位确认后做空。"
        "突破需超过均线一定幅度过滤假突破，大K线用 ATR 倍数判定适配波动率。"
        "出场包含固定止损、止盈、移动止损、时间止损。"
        "触发条件较严格，信号频率天然较低，适合趋势启动后的回调入场。"
    )

    # 信号参数
    ma_fast: int = 30                # 快均线周期（0=不使用）
    ma_slow: int = 60                # 慢均线周期（0=不使用）
    breakout_pct: float = 0.005      # 突破需超过均线的幅度（0.5%）
    trend_lookback: int = 21         # 趋势回看天数（以突破前一日为基准）
    trend_return: float = 0.08       # 趋势阈值（±8%）
    big_candle_atr: float = 1.5      # 大阳/大阴线：收盘涨幅 >= N 倍 ATR（百分比）
    atr_window: int = 14             # ATR 计算周期
    fib_levels: tuple = (0.382, 0.5, 0.618)  # 黄金分割回调位
    require_confirm: bool = True     # 回调位是否需要收盘站稳确认
    max_wait: int = 10               # 触发后最多等待 K线根数

    # 出场参数
    stop_loss_atr: float = 2.0       # 固定止损（ATR 倍数，0=关闭）
    take_profit_atr: float = 4.0     # 止盈（ATR 倍数，0=关闭）
    trailing_stop_atr: float = 1.5   # 移动止损（ATR 倍数，0=关闭）
    max_hold_bars: int = 40          # 时间止损（K线根数，0=关闭）

    # 运行参数
    fixed_size: int = 1              # 每次开仓手数
    bar_interval: str = "d"          # K 线周期：d/5m/15m/30m/1h/1m
    daily_close_time: str = "15:00"  # 日线收盘时刻（仅 bar_interval="d" 时生效）
    history_days: int = 120          # 初始化加载的历史天数（预热）

    # 运行变量
    atr_value: float = 0.0
    ma_fast_value: float = 0.0
    ma_slow_value: float = 0.0
    pending_state: str = "none"      # none / long / short

    parameters = [
        "ma_fast", "ma_slow", "breakout_pct", "trend_lookback", "trend_return",
        "big_candle_atr", "atr_window", "require_confirm", "max_wait",
        "stop_loss_atr", "take_profit_atr",
        "trailing_stop_atr", "max_hold_bars", "fixed_size",
        "bar_interval", "daily_close_time", "history_days",
    ]
    variables = ["atr_value", "ma_fast_value", "ma_slow_value", "pending_state"]
    param_descriptions = {
        "ma_fast": "快均线周期（0=不使用），突破此均线视为趋势启动",
        "ma_slow": "慢均线周期（0=不使用），与快线任一突破即可触发",
        "breakout_pct": "突破需超过均线的幅度（如 0.005 = 0.5%），过滤贴着均线走的假突破",
        "trend_lookback": "趋势回看K线根数（以突破前一根收盘为基准，不含突破当日）",
        "trend_return": "趋势阈值（如 0.08 = ±8%），前期涨跌幅超过此值才认定趋势",
        "big_candle_atr": "大阳/大阴线判定：收盘涨幅 >= N 倍 ATR（百分比）",
        "atr_window": "ATR 计算周期（K线根数）",
        "require_confirm": "回调位是否需要收盘站稳确认（True=更严格）",
        "max_wait": "突破触发后最多等待回调的 K线根数，超时失效",
        "stop_loss_atr": "固定止损（ATR 倍数，0=关闭）",
        "take_profit_atr": "止盈（ATR 倍数，0=关闭）",
        "trailing_stop_atr": "移动止损（ATR 倍数，0=关闭）",
        "max_hold_bars": "时间止损（持仓满 N 根K线平仓，0=关闭）",
        "fixed_size": "每次开仓手数",
        "bar_interval": "K线周期：d=日线 / 5m / 15m / 30m / 1h / 1m，分钟线用实盘1分钟聚合",
        "daily_close_time": "日线收盘时刻（HH:MM），仅 bar_interval=d 时生效",
        "history_days": "初始化加载的历史天数（预热，按 bar_interval 周期加载）",
    }
    variable_descriptions = {
        "atr_value": "当前 ATR 波动率",
        "ma_fast_value": "当前快均线值",
        "ma_slow_value": "当前慢均线值",
        "pending_state": "等待回调状态（none/long/short）",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting) -> None:  # noqa: ANN001
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self._target_interval = _BAR_INTERVAL_MAP.get(self.bar_interval, Interval.DAILY)
        self._build_bar_generator()
        # ArrayManager 容纳：慢均线 + 趋势回看 + ATR，自适应参数（避免写死下限）
        size = max(self.ma_slow, self.trend_lookback, self.atr_window) + 5
        self.am = ArrayManager(size=size)
        # pending 状态机
        self._pending: dict | None = None
        # 持仓跟踪
        self._entry_price: float = 0.0
        self._highest: float = 0.0
        self._lowest: float = 0.0
        self._hold_bars: int = 0
        self._atr_prev: float = 0.0

    def _build_bar_generator(self) -> None:
        """根据 bar_interval 构造 BarGenerator。"""
        from datetime import time as dtime
        bi = self.bar_interval
        if bi == "d":
            hh, mm = (int(x) for x in self.daily_close_time.split(":"))
            self.bg = BarGenerator(
                self.on_bar, interval=Interval.DAILY,
                daily_end=dtime(hh, mm),
                on_window_bar=self.on_daily_bar,
            )
        elif bi == "1m":
            self.bg = None  # 1 分线无需聚合，on_bar 直接处理
        elif bi == "1h":
            self.bg = BarGenerator(
                self.on_bar, interval=Interval.HOUR,
                window=1, on_window_bar=self.on_daily_bar,
            )
        else:  # "5m" / "15m" / "30m"
            window = int(bi[:-1])  # 去掉末尾的 "m"
            self.bg = BarGenerator(
                self.on_bar, window=window,
                on_window_bar=self.on_daily_bar,
            )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def on_init(self) -> None:
        self.write_log("strategy on_init")
        self._target_interval = _BAR_INTERVAL_MAP.get(self.bar_interval, Interval.DAILY)
        self._build_bar_generator()
        size = max(self.ma_slow, self.trend_lookback, self.atr_window) + 5
        self.am = ArrayManager(size=size)
        self._pending = None
        self._entry_price = 0.0
        self._highest = 0.0
        self._lowest = 0.0
        self._hold_bars = 0
        self._atr_prev = 0.0
        self.atr_value = 0.0
        self.ma_fast_value = 0.0
        self.ma_slow_value = 0.0
        self.pending_state = "none"
        self.pos = 0.0
        # 预热历史 K 线。
        # - 日线：直接加载日线 K 线；
        # - 1 分线：直接加载 1 分线；
        # - 5m/15m/30m/1h：加载 1 分线并通过 BarGenerator 聚合为目标周期
        #   （避免使用可能过期的缓存聚合 K 线——1 分线总是最新的）。
        if self.bar_interval == "d":
            self.load_bar(self.history_days, interval=Interval.DAILY,
                          callback=self.on_daily_bar)
        elif self.bar_interval == "1m":
            self.load_bar(self.history_days, interval=Interval.MINUTE,
                          callback=self.on_daily_bar)
        else:
            self.load_bar(self.history_days, interval=Interval.MINUTE,
                          callback=self.on_bar)

    def on_start(self) -> None:
        self.write_log("strategy on_start")

    def on_stop(self) -> None:
        self.write_log("strategy on_stop")

    def on_tick(self, tick: TickData) -> None:
        if self.bg is not None:
            self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """收到 K 线。

        - 1 分线模式（bar_interval="1m"）：直接进入 ``on_daily_bar``；
        - 其它模式：若是目标周期的 K 线（回测逐根重放，或 BarGenerator
          已聚合）→ 直接进入 ``on_daily_bar``；否则（实盘 1 分钟线）
          → 交给 BarGenerator 聚合为目标周期。
        """
        if self.bar_interval == "1m" or bar.interval == self._target_interval:
            self.on_daily_bar(bar)
        elif self.bg is not None:
            self.bg.update_bar(bar)

    # ------------------------------------------------------------------
    # 核心逻辑
    # ------------------------------------------------------------------

    def on_daily_bar(self, bar: BarData) -> None:
        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        # 指标更新
        atr_arr = am.atr(self.atr_window, array=True)
        if atr_arr.size >= 2 and not np.isnan(atr_arr[-2]):
            self._atr_prev = float(atr_arr[-2])
            self.atr_value = self._atr_prev
        self.ma_fast_value = float(am.ema(self.ma_fast)) if self.ma_fast > 0 else 0.0
        self.ma_slow_value = float(am.ema(self.ma_slow)) if self.ma_slow > 0 else 0.0

        # ---- 持仓中：移动止损 / 时间止损 ----
        if self.pos != 0:
            if self._check_exits(bar):
                self.put_event()
                return

        # ---- 无持仓：先处理 pending（等待回调），再检查新突破 ----
        if self.pos == 0:
            entry = self._process_pending(bar)
            if not entry and self._pending is None:
                self._check_breakout(bar)

        self.put_event()

    def _process_pending(self, bar: BarData) -> bool:
        """推进 pending 状态机；回调确认则开仓，返回是否已开仓。"""
        if self._pending is None:
            return False

        self._pending["age"] += 1
        if self._pending["age"] > self.max_wait:
            self._pending = None
            self.pending_state = "none"
            return False

        direction = self._pending["direction"]
        levels = self._pending["levels"]
        entered = False

        if direction == "long":
            # 失效：跌破触发阳线最低点
            if bar.low_price < self._pending["trigger_low"]:
                self._pending = None
                self.pending_state = "none"
                return False
            for lvl in levels:
                if bar.low_price <= lvl <= bar.high_price:
                    ok = (bar.close_price > bar.open_price and
                          bar.close_price >= lvl) if self.require_confirm else True
                    if ok:
                        self._open_position(bar, long_side=True)
                        entered = True
                        break
        else:  # short
            if bar.high_price > self._pending["trigger_high"]:
                self._pending = None
                self.pending_state = "none"
                return False
            for lvl in levels:
                if bar.low_price <= lvl <= bar.high_price:
                    ok = (bar.close_price < bar.open_price and
                          bar.close_price <= lvl) if self.require_confirm else True
                    if ok:
                        self._open_position(bar, long_side=False)
                        entered = True
                        break

        if entered:
            self._pending = None
            self.pending_state = "none"
        return entered

    def _check_breakout(self, bar: BarData) -> None:
        """检查均线突破 + 大K线 + 前期趋势，成立则建立 pending。"""
        am = self.am
        if am.size < 2 or self._atr_prev <= 0:
            return
        if am.size < self.trend_lookback + 2:
            return

        close = am.close_array.astype(float)
        prev_c = close[-2]
        curr_c = close[-1]
        if prev_c <= 0:
            return

        # 均线突破判断（需超过 breakout_pct 幅度）
        break_up = False
        break_down = False
        if self.ma_fast > 0:
            ma_fast_arr = am.ema(self.ma_fast, array=True)
            if not np.isnan(ma_fast_arr[-1]):
                th = self.breakout_pct * ma_fast_arr[-1]
                if curr_c > ma_fast_arr[-1] + th and prev_c <= ma_fast_arr[-1]:
                    break_up = True
                if curr_c < ma_fast_arr[-1] - th and prev_c >= ma_fast_arr[-1]:
                    break_down = True
        if self.ma_slow > 0:
            ma_slow_arr = am.ema(self.ma_slow, array=True)
            if not np.isnan(ma_slow_arr[-1]):
                th = self.breakout_pct * ma_slow_arr[-1]
                if curr_c > ma_slow_arr[-1] + th and prev_c <= ma_slow_arr[-1]:
                    break_up = True
                if curr_c < ma_slow_arr[-1] - th and prev_c >= ma_slow_arr[-1]:
                    break_down = True
        if not break_up and not break_down:
            return

        # 大阳/大阴线：收盘涨幅 >= big_candle_atr 倍 ATR（百分比）
        pct_ret = (curr_c - prev_c) / prev_c
        atr_pct = self._atr_prev / prev_c
        is_big = abs(pct_ret) >= self.big_candle_atr * atr_pct
        is_bull = curr_c > bar.open_price
        is_bear = curr_c < bar.open_price

        # 前期趋势：以突破前一日收盘为基准
        past_ret = curr_c / close[-1 - self.trend_lookback] - 1.0 \
            if am.size > self.trend_lookback + 1 else np.nan
        if not np.isfinite(past_ret):
            return

        opn = bar.open_price
        if break_up and is_big and is_bull and past_ret <= -self.trend_return:
            body_low = min(opn, curr_c)
            body_high = max(opn, curr_c)
            rng = body_high - body_low
            levels = sorted(body_high - rng * f for f in self.fib_levels)
            self._pending = {
                "direction": "long", "age": 0,
                "trigger_low": bar.low_price, "trigger_high": bar.high_price,
                "levels": levels,
            }
            self.pending_state = "long"
            self.write_log(
                f"突破+大阳建立做多等待: close={curr_c:.2f}, "
                f"回调位={[round(l, 2) for l in levels]}"
            )
        elif break_down and is_big and is_bear and past_ret >= self.trend_return:
            body_low = min(opn, curr_c)
            body_high = max(opn, curr_c)
            rng = body_high - body_low
            levels = sorted(body_low + rng * f for f in self.fib_levels)
            self._pending = {
                "direction": "short", "age": 0,
                "trigger_low": bar.low_price, "trigger_high": bar.high_price,
                "levels": levels,
            }
            self.pending_state = "short"
            self.write_log(
                f"跌破+大阴建立做空等待: close={curr_c:.2f}, "
                f"回调位={[round(l, 2) for l in levels]}"
            )

    def _open_position(self, bar: BarData, long_side: bool) -> None:
        """回调确认后以收盘价 ± 缓冲下单开仓，并挂固定止损/止盈本地停止单。"""
        pricetick = self._get_pricetick()
        price = bar.close_price

        if long_side:
            self.buy(_tick_up(price, pricetick), self.fixed_size)
            self._entry_price = price
            self._highest = bar.high_price
            self._lowest = price
            self.write_log(
                f"黄金分割回调确认做多: price={price:.2f}, ATR={self._atr_prev:.2f}"
            )
            self._place_stop_orders(is_long=True)
        else:
            self.short(_tick_down(price, pricetick), self.fixed_size)
            self._entry_price = price
            self._lowest = bar.low_price
            self._highest = price
            self.write_log(
                f"黄金分割回调确认做空: price={price:.2f}, ATR={self._atr_prev:.2f}"
            )
            self._place_stop_orders(is_long=False)

        self._hold_bars = 0

    def _place_stop_orders(self, is_long: bool) -> None:
        a = self._atr_prev
        vol = abs(self.pos) if self.pos else self.fixed_size
        if self.stop_loss_atr > 0:
            if is_long:
                self.sell(self._entry_price - self.stop_loss_atr * a, vol, stop=True)
            else:
                self.cover(self._entry_price + self.stop_loss_atr * a, vol, stop=True)
        if self.take_profit_atr > 0:
            if is_long:
                self.sell(self._entry_price + self.take_profit_atr * a, vol, stop=True)
            else:
                self.cover(self._entry_price - self.take_profit_atr * a, vol, stop=True)

    def _check_exits(self, bar: BarData) -> bool:
        """移动止损 / 时间止损（按收盘价判断）。触发则平仓并返回 True。"""
        self._hold_bars += 1
        self._highest = max(self._highest, bar.high_price)
        self._lowest = min(self._lowest, bar.low_price)
        a = self._atr_prev
        price = bar.close_price
        pricetick = self._get_pricetick()

        if self.trailing_stop_atr > 0 and a > 0:
            if self.pos > 0 and self._highest > 0:
                if (self._highest - price) >= self.trailing_stop_atr * a:
                    self.sell(_tick_down(price, pricetick), abs(self.pos))
                    self.write_log(
                        f"移动止损平多: price={price:.2f}, 高点={self._highest:.2f}"
                    )
                    self._reset_position()
                    return True
            elif self.pos < 0 and self._lowest > 0:
                if (price - self._lowest) >= self.trailing_stop_atr * a:
                    self.cover(_tick_up(price, pricetick), abs(self.pos))
                    self.write_log(
                        f"移动止损平空: price={price:.2f}, 低点={self._lowest:.2f}"
                    )
                    self._reset_position()
                    return True

        if self.max_hold_bars > 0 and self._hold_bars >= self.max_hold_bars:
            if self.pos > 0:
                self.sell(_tick_down(price, pricetick), abs(self.pos))
            else:
                self.cover(_tick_up(price, pricetick), abs(self.pos))
            self.write_log(
                f"时间止损平仓: price={price:.2f}, 持仓={self._hold_bars}根"
            )
            self._reset_position()
            return True

        return False

    def _reset_position(self) -> None:
        self._entry_price = 0.0
        self._highest = 0.0
        self._lowest = 0.0
        self._hold_bars = 0

    def _get_pricetick(self) -> float:
        engine = self.strategy_engine
        if hasattr(engine, "main_engine") and engine.main_engine:
            oms = getattr(engine.main_engine, "oms", None)
            if oms:
                contract = oms.get_contract(self.vt_symbol)
                if contract:
                    return contract.pricetick
        if hasattr(engine, "pricetick"):
            return engine.pricetick
        return 0.01


def _tick_up(price: float, pricetick: float) -> float:
    return round(price + 5 * pricetick, 6) if pricetick else price


def _tick_down(price: float, pricetick: float) -> float:
    return round(price - 5 * pricetick, 6) if pricetick else price
