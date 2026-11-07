"""策略1：连续小阳/小阴线反转（事件驱动版）。

将原始自包含回测函数 ``strategy1_continuous_move`` 改写为
``StrategyTemplate`` 子类，在实盘/回测中以事件驱动方式运行。

逻辑要点（与回测版一致）：
- 做多：前期累计跌幅超过 down_return（默认 -8%）→ 最近 consec_days
  根中至少 min_consec 根是小阳线（涨幅在 atr_mult_min~atr_mult_max
  倍 ATR 之间）→ 次日开盘做多。
- 做空：前期累计涨幅超过 up_return（默认 +8%）→ 最近 consec_days 根
  中至少 min_consec 根是小阴线 → 次日开盘做空。
- 多空信号互斥：同日同时触发不交易。

执行口径（与平台其他日内策略一致）：在日线收盘时生成信号，立即以
收盘价 ± 缓冲价发出限价单。回测中该限价单在次日 K 线以开盘价撮合
（限价买单 ``min(委托价, 开盘价)``），等价于"次日开盘价执行"；
实盘中订单留在盘口，于下一交易日开盘成交。

出场体系（四重）：
1. 固定止损：开仓价 ∓ stop_loss_atr × ATR，本地停止单盘中触发；
2. 止盈：开仓价 ± take_profit_atr × ATR，本地停止单；
3. 移动止损：从持仓最高/最低价回撤 trailing_stop_atr × ATR（收盘价判断）；
4. 时间止损：持仓满 max_hold_bars 根日线 K 线平仓。

ATR 用"前一日"值（at bar 收盘时已知），避免未来函数。

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


class ContinuousMoveStrategy(StrategyTemplate):
    """连续小阳/小阴线反转策略（事件驱动版）。"""

    author = "custom-trader"
    display_name = "连续小阳反转"
    description = (
        "连续小阳/小阴线反转策略：前期累计下跌（或上涨）后，出现连续小阳（或小阴）线"
        "即视为反转信号，次日开盘做多（或做空）。"
        "幅度阈值用 ATR 倍数自适应，连续条件放宽为窗口内满足 M 根即可。"
        "出场包含固定止损、止盈、移动止损、时间止损四重机制。"
        "适合底部/顶部反转行情，震荡市中可能频繁止损。"
    )

    # 信号参数
    down_lookback: int = 20          # 做多前：下跌回看天数
    down_return: float = -0.08       # 做多前：累计跌幅阈值（-8%）
    up_lookback: int = 20            # 做空前：上涨回看天数
    up_return: float = 0.08          # 做空前：累计涨幅阈值（+8%）
    consec_days: int = 3             # 连续K线判定窗口
    min_consec: int = 2              # 窗口中至少满足的根数
    atr_mult_min: float = 0.3        # 小阳/小阴线最小幅度 = N 倍 ATR
    atr_mult_max: float = 1.2        # 小阳/小阴线最大幅度 = N 倍 ATR
    atr_window: int = 14             # ATR 计算周期

    # 出场参数
    stop_loss_atr: float = 2.0       # 固定止损（ATR 倍数，0=关闭）
    take_profit_atr: float = 4.0     # 止盈（ATR 倍数，0=关闭）
    trailing_stop_atr: float = 1.5   # 移动止损（ATR 倍数，0=关闭）
    max_hold_bars: int = 30          # 时间止损（K线根数，0=关闭）

    # 运行参数
    fixed_size: int = 1              # 每次开仓手数
    bar_interval: str = "d"          # K 线周期：d/5m/15m/30m/1h/1m
    daily_close_time: str = "15:00"  # 日线收盘时刻（仅 bar_interval="d" 时生效）
    history_days: int = 90           # 初始化加载的历史天数（预热）

    # 运行变量
    atr_value: float = 0.0
    signal_long: bool = False
    signal_short: bool = False

    parameters = [
        "down_lookback", "down_return", "up_lookback", "up_return",
        "consec_days", "min_consec", "atr_mult_min", "atr_mult_max",
        "atr_window", "stop_loss_atr", "take_profit_atr",
        "trailing_stop_atr", "max_hold_bars", "fixed_size",
        "bar_interval", "daily_close_time", "history_days",
    ]
    variables = ["atr_value", "signal_long", "signal_short"]
    param_descriptions = {
        "down_lookback": "做多前累计跌幅的回看K线根数",
        "down_return": "做多前累计跌幅阈值（如 -0.08 = 下跌 8% 后才看反弹）",
        "up_lookback": "做空前累计涨幅的回看K线根数",
        "up_return": "做空前累计涨幅阈值（如 0.08 = 上涨 8% 后才看回调）",
        "consec_days": "连续K线判定窗口（最近 N 根中至少 min_consec 根满足）",
        "min_consec": "窗口中至少满足的根数（放宽，避免信号过少）",
        "atr_mult_min": "小阳/小阴线最小幅度 = N 倍 ATR",
        "atr_mult_max": "小阳/小阴线最大幅度 = N 倍 ATR",
        "atr_window": "ATR 计算周期（K线根数）",
        "stop_loss_atr": "固定止损（ATR 倍数，0=关闭）",
        "take_profit_atr": "止盈（ATR 倍数，0=关闭）",
        "trailing_stop_atr": "移动止损（ATR 倍数，0=关闭）",
        "max_hold_bars": "时间止损（持仓满 N 根K线平仓，0=关闭）",
        "fixed_size": "每次开仓手数",
        "bar_interval": "K线周期：d=日线 / 5m / 15m / 30m / 1h / 1m，分钟线用实盘1分钟聚合",
        "daily_close_time": "日线收盘时刻（HH:MM），仅 bar_interval=d 时生效，无夜盘品种填 15:00",
        "history_days": "初始化加载的历史天数（预热，按 bar_interval 周期加载）",
    }
    variable_descriptions = {
        "atr_value": "当前 ATR 波动率（前一日值，用于止损价计算）",
        "signal_long": "最新一日是否生成做多信号",
        "signal_short": "最新一日是否生成做空信号",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting) -> None:  # noqa: ANN001
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self._target_interval = _BAR_INTERVAL_MAP.get(self.bar_interval, Interval.DAILY)
        self._build_bar_generator()
        size = max(
            self.down_lookback, self.up_lookback,
            self.consec_days, self.atr_window,
        ) + 5
        self.am = ArrayManager(size=size)
        # 持仓跟踪
        self._entry_price: float = 0.0
        self._highest: float = 0.0      # 持仓期间最高价（多头）
        self._lowest: float = 0.0       # 持仓期间最低价（空头）
        self._hold_bars: int = 0        # 已持仓 K 线根数
        self._atr_prev: float = 0.0     # 前一日/前一根 ATR（用于止损价计算）

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
        size = max(
            self.down_lookback, self.up_lookback,
            self.consec_days, self.atr_window,
        ) + 5
        self.am = ArrayManager(size=size)
        self._entry_price = 0.0
        self._highest = 0.0
        self._lowest = 0.0
        self._hold_bars = 0
        self._atr_prev = 0.0
        self.atr_value = 0.0
        self.signal_long = False
        self.signal_short = False
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

        # 前一日 ATR（at 收盘时已知，避免未来函数）
        atr_arr = am.atr(self.atr_window, array=True)
        if atr_arr.size >= 2 and not np.isnan(atr_arr[-2]):
            self._atr_prev = float(atr_arr[-2])
            self.atr_value = self._atr_prev

        # ---- 持仓中：先做移动止损 / 时间止损（按收盘价判断） ----
        if self.pos != 0:
            if self._check_exits(bar):
                self.put_event()
                return

        # ---- 无持仓：用今日收盘生成信号，立即以收盘价 ± 缓冲下单 ----
        if self.pos == 0:
            long_sig, short_sig = self._generate_signal()
            self.signal_long = long_sig
            self.signal_short = short_sig
            if long_sig or short_sig:
                self._open_position(bar, long_sig)

        self.put_event()

    def _generate_signal(self) -> tuple[bool, bool]:
        """用今日收盘数据生成信号。返回 (做多, 做空)。"""
        am = self.am
        need = max(self.down_lookback, self.up_lookback, self.atr_window) + 1
        if am.size < need:
            return False, False

        close = am.close_array.astype(float)
        pct = np.zeros_like(close)
        with np.errstate(invalid="ignore", divide="ignore"):
            pct[1:] = (close[1:] - close[:-1]) / close[:-1]
        pct[~np.isfinite(pct)] = 0.0

        # ATR 转价格百分比；今日判定用 shift(1) 后的阈值（昨日 ATR/昨日收盘）
        atr_arr = am.atr(self.atr_window, array=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            atr_pct = atr_arr / close
        atr_pct_prev = np.roll(atr_pct, 1)
        atr_pct_prev[0] = np.nan
        lo = self.atr_mult_min * atr_pct_prev
        hi = self.atr_mult_max * atr_pct_prev

        up_day = (pct >= lo) & (pct <= hi) & (pct > 0)
        down_day = (pct <= -lo) & (pct >= -hi) & (pct < 0)

        consec_up = _rolling_min_true(up_day, self.consec_days, self.min_consec)
        consec_down = _rolling_min_true(down_day, self.consec_days, self.min_consec)

        with np.errstate(invalid="ignore"):
            past_ret_down = close / np.roll(close, self.down_lookback) - 1.0
            past_ret_up = close / np.roll(close, self.up_lookback) - 1.0
        past_ret_down[:self.down_lookback] = np.nan
        past_ret_up[:self.up_lookback] = np.nan

        long_cond = (past_ret_down <= self.down_return) & consec_up
        short_cond = (past_ret_up >= self.up_return) & consec_down

        both = long_cond & short_cond
        long_sig = bool(long_cond[-1] and not both[-1])
        short_sig = bool(short_cond[-1] and not both[-1])
        return long_sig, short_sig

    def _open_position(self, bar: BarData, long_side: bool) -> None:
        """以收盘价 ± 缓冲下单开仓，并挂固定止损/止盈本地停止单。"""
        pricetick = self._get_pricetick()
        price = bar.close_price

        if self._atr_prev <= 0:
            self.write_log("开仓信号触发但 ATR 缺失，放弃本次信号")
            return

        if long_side:
            order_price = _tick_up(price, pricetick)
            self.buy(order_price, self.fixed_size)
            self._entry_price = price
            self._highest = bar.high_price
            self._lowest = price
            self.write_log(
                f"连续小阳反转做多: price={price:.2f}, ATR={self._atr_prev:.2f}"
            )
            self._place_stop_orders(is_long=True)
        else:
            order_price = _tick_down(price, pricetick)
            self.short(order_price, self.fixed_size)
            self._entry_price = price
            self._lowest = bar.low_price
            self._highest = price
            self.write_log(
                f"连续小阴反转做空: price={price:.2f}, ATR={self._atr_prev:.2f}"
            )
            self._place_stop_orders(is_long=False)

        self._hold_bars = 0

    def _place_stop_orders(self, is_long: bool) -> None:
        """开仓后挂固定止损 / 止盈本地停止单。"""
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
        """移动止损 / 时间止损（按收盘价判断）。触发则平仓并返回 True。

        注意：固定止损/止盈由本地停止单在盘中触发，引擎会直接平仓；
        这里只处理移动止损与时间止损。
        """
        self._hold_bars += 1
        self._highest = max(self._highest, bar.high_price)
        self._lowest = min(self._lowest, bar.low_price)
        a = self._atr_prev
        price = bar.close_price
        pricetick = self._get_pricetick()

        # 移动止损
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

        # 时间止损
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
        """从合约/引擎获取 tick size，兼容实盘和回测。"""
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


# ----------------------------------------------------------------------
# 模块级辅助函数
# ----------------------------------------------------------------------

def _rolling_min_true(arr: np.ndarray, window: int, min_count: int) -> np.ndarray:
    """滚动窗口内 True 的个数 >= min_count。前 window-1 个为 False。"""
    n = arr.size
    out = np.zeros(n, dtype=bool)
    if window <= 0 or n < window or min_count <= 0:
        return out
    # 前置一个 0 的累加和：padded[i+1] = sum(arr[:i+1])
    padded = np.concatenate(([0], np.cumsum(arr.astype(int))))
    # 窗口 [i-window+1, i] 的和 = padded[i+1] - padded[i-window+1]
    win_sum = padded[window:] - padded[: n - window + 1]
    out[window - 1:] = win_sum >= min_count
    return out


def _tick_up(price: float, pricetick: float) -> float:
    return round(price + 5 * pricetick, 6) if pricetick else price


def _tick_down(price: float, pricetick: float) -> float:
    return round(price - 5 * pricetick, 6) if pricetick else price
