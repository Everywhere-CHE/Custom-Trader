"""趋势跟随策略（3分钟K线 + 日线趋势过滤 + 20日高低点关键位）。

双时间框架系统：日线判定大方向（MA60 + 阈值），3分钟K线寻找入场时机。
只在顺势方向交易，回调至MA20附近且突破关键位时入场。
持仓后通过移动止损、MA30止盈、前高/前低部分止盈三层机制管理风险。
"""

from app.core.constant import Interval
from app.core.object import BarData, TickData
from app.core.strategy.array_manager import ArrayManager
from app.core.strategy.bar_generator import BarGenerator
from app.core.strategy.template import StrategyTemplate


class TrendFollowingStrategy(StrategyTemplate):
    """3分钟趋势跟随策略（日线趋势过滤 + 关键位）。

    双时间框架趋势跟随系统，结合日线趋势判断和3分钟K线入场信号。
    只在顺势方向交易，避免逆势操作。适合有明显趋势的行情，
    震荡市中会频繁止损，建议配合趋势行情使用。

    交易逻辑：
    1. 日线趋势判断：价格在MA60上方1.5%以上为多头趋势，下方1.5%为空头
    2. 关键位过滤：接近前20日高/低点0.5%以内时暂停开仓，避免追高杀跌
    3. 入场：3分钟K线回调至MA20附近，且价格突破前20日高/低点时开仓
    4. 移动止损：持仓后从最高/最低点回撤1.5倍ATR时平仓
    5. 止盈：跌破/升破MA30时平仓
    6. 部分止盈：触及前20日高/低点时平一半仓位
    """

    author = "custom-trader"
    display_name = "双时间框架趋势跟随"
    description = (
        "双时间框架趋势跟随策略：先用日线MA60判断大趋势方向（偏离1.5%以上认定趋势），"
        "只在顺势方向交易，避免逆势操作。"
        "3分钟K线回调至MA20附近（±0.5%）且突破前20日高/低点时入场开仓。"
        "持仓后有三层风险保护：从最高/最低点回撤1.5倍ATR触发移动止损、"
        "跌破/升破MA30触发止盈、触及前高/前低时平掉一半仓位锁定利润。"
        "策略逻辑清晰，适合有明显趋势的行情（如股指期货趋势行情），"
        "震荡市中可能会反复被扫止损，建议配合趋势行情使用。"
    )

    # 参数
    ma_short: int = 20          # 3分钟K线MA周期
    ma_stop: int = 30           # 3分钟K线MA30止盈线
    atr_period: int = 14        # ATR计算周期
    trail_mult: float = 1.5     # 移动止损ATR倍数
    trend_threshold: float = 0.015   # 日线趋势判定阈值
    key_level_threshold: float = 0.005  # 关键位暂停开仓阈值
    fixed_size: int = 1         # 开仓手数

    # 运行变量
    daily_trend: str = "none"
    daily_ma60: float = 0.0
    daily_high20: float = 0.0
    daily_low20: float = 0.0
    ma20: float = 0.0
    ma30: float = 0.0
    atr: float = 0.0

    parameters = [
        "ma_short", "ma_stop", "atr_period", "trail_mult",
        "trend_threshold", "key_level_threshold", "fixed_size",
    ]
    variables = [
        "daily_trend", "daily_ma60", "daily_high20", "daily_low20",
        "ma20", "ma30", "atr",
    ]
    param_descriptions = {
        "ma_short": "3分钟K线的MA周期（回调入场线），价格回调至此线附近且突破关键位时入场，数值越小对回调越敏感",
        "ma_stop": "3分钟K线的MA30止盈线，跌破/升破此线时平仓，通常设为比ma_short大10-20",
        "atr_period": "ATR波动率计算周期，用于移动止损的距离计算，越大止损越宽",
        "trail_mult": "移动止损回撤的ATR倍数，如1.5表示从最高点回撤1.5倍ATR时平仓，越大止损越宽松",
        "trend_threshold": "日线趋势判定阈值（如0.015=1.5%），价格在MA60上下超过此比例才认定为趋势行情，否则为震荡市不交易",
        "key_level_threshold": "关键位附近暂停开仓的阈值（如0.005=0.5%），接近前20日高/低点时不追单，避免买在最高点",
        "fixed_size": "每次开仓手数，建议根据账户资金合理设置",
    }
    variable_descriptions = {
        "daily_trend": "日线趋势方向（up=多头/down=空头/sideways=震荡），基于MA60和阈值判定",
        "daily_ma60": "日线60周期均线，用于判断长期趋势方向",
        "daily_high20": "前20日最高价，突破此价位做多，接近时暂停开仓",
        "daily_low20": "前20日最低价，跌破此价位做空，接近时暂停开仓",
        "ma20": "3分钟K线MA20，价格回调至此线附近才是入场时机",
        "ma30": "3分钟K线MA30，作为止盈线使用",
        "atr": "3分钟K线ATR（平均真实波幅），用于计算移动止损距离",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting) -> None:  # noqa: ANN001
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        # 用 window=3 把 1 分钟 K 线聚合成 3 分钟 K 线
        self.bg = BarGenerator(self.on_bar, window=3, on_window_bar=self.on_3min_bar)
        self.am = ArrayManager(size=max(self.ma_stop + 5, 50))
        self.daily_am = ArrayManager(size=80)
        self._highest = 0.0
        self._lowest = 0.0
        self._entry_price = 0.0

    def on_init(self) -> None:
        self.write_log("strategy on_init")
        # 1分钟K线预热（用于聚合3分钟K线 + 计算指标）
        self.load_bar(5, interval=Interval.MINUTE)
        # 日线数据预热（计算趋势方向）
        self.load_bar(60, interval=Interval.DAILY, callback=self.on_daily_bar)

    def on_start(self) -> None:
        self.write_log("strategy on_start")

    def on_stop(self) -> None:
        self.write_log("strategy on_stop")

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """1分钟K线（用于聚合3分钟K线 + 更新指标）。"""
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        self.ma20 = self.am.sma(self.ma_short)
        self.ma30 = self.am.sma(self.ma_stop)
        self.atr = self.am.atr(self.atr_period)
        # 传给 BarGenerator 聚合为3分钟K线
        self.bg.update_bar(bar)

    def on_daily_bar(self, bar: BarData) -> None:
        """日线K线，用于计算趋势方向。"""
        self.daily_am.update_bar(bar)
        if not self.daily_am.inited:
            return
        self.daily_ma60 = self.daily_am.sma(60)
        self.daily_high20 = float(self.daily_am.high()[-20:].max())
        self.daily_low20 = float(self.daily_am.low[-20:].min())
        close = bar.close_price
        if close > self.daily_ma60 * (1 + self.trend_threshold):
            self.daily_trend = "up"
        elif close < self.daily_ma60 * (1 - self.trend_threshold):
            self.daily_trend = "down"
        else:
            self.daily_trend = "sideways"

    def on_3min_bar(self, bar: BarData) -> None:
        """3分钟K线闭合时执行策略逻辑。"""
        if self.daily_trend == "none":
            return

        close = bar.close_price
        high = bar.high_price
        low = bar.low_price

        # 关键位过滤：接近日线前高/前低时暂停开仓
        if self.daily_trend != "sideways" and self.daily_high20 > 0:
            if abs(close - self.daily_high20) / close < self.key_level_threshold:
                self.write_log("关键位附近，暂停开仓")
                self.put_event()
                return
            if abs(close - self.daily_low20) / close < self.key_level_threshold:
                self.write_log("关键位附近，暂停开仓")
                self.put_event()
                return

        # 入场（无持仓时）
        if self.pos == 0:
            if self.daily_trend == "up" and self.ma20 > 0:
                if close >= self.ma20 * 0.995 and close <= self.ma20 * 1.005:
                    if self.daily_high20 > 0 and close > self.daily_high20 * 0.99:
                        self.buy(close + 5 * (close * 0.0001), self.fixed_size)
                        self._entry_price = close
                        self._highest = high
                        self.write_log(f"多头入场: {close:.2f}")
            elif self.daily_trend == "down" and self.ma20 > 0:
                if close <= self.ma20 * 1.005 and close >= self.ma20 * 0.995:
                    if self.daily_low20 > 0 and close < self.daily_low20 * 1.01:
                        self.short(close - 5 * (close * 0.0001), self.fixed_size)
                        self._entry_price = close
                        self._lowest = low
                        self.write_log(f"空头入场: {close:.2f}")

        # 持仓管理
        elif self.pos > 0:
            self._highest = max(self._highest, high)
            if self.atr > 0:
                trail_stop = self._highest - self.trail_mult * self.atr
                if close < trail_stop:
                    self.sell(close - 5 * (close * 0.0001), abs(self.pos))
                    self.write_log(f"多单移动止损: {close:.2f}")
                    self.put_event()
                    return
            if self.ma30 > 0 and close < self.ma30:
                self.sell(close - 5 * (close * 0.0001), abs(self.pos))
                self.write_log(f"多单MA30止盈: {close:.2f}")
                self.put_event()
                return
            if self.daily_high20 > 0 and close >= self.daily_high20 and self.pos >= 2:
                half = int(self.pos / 2) or 1
                self.sell(close - 5 * (close * 0.0001), half)
                self.write_log(f"多单到达前高，平一半: {close:.2f}")

        elif self.pos < 0:
            self._lowest = min(self._lowest, low)
            if self.atr > 0:
                trail_stop = self._lowest + self.trail_mult * self.atr
                if close > trail_stop:
                    self.cover(close + 5 * (close * 0.0001), abs(self.pos))
                    self.write_log(f"空单移动止损: {close:.2f}")
                    self.put_event()
                    return
            if self.ma30 > 0 and close > self.ma30:
                self.cover(close + 5 * (close * 0.0001), abs(self.pos))
                self.write_log(f"空单MA30止盈: {close:.2f}")
                self.put_event()
                return
            if self.daily_low20 > 0 and close <= self.daily_low20 and abs(self.pos) >= 2:
                half = int(abs(self.pos) / 2) or 1
                self.cover(close + 5 * (close * 0.0001), half)
                self.write_log(f"空单到达前低，平一半: {close:.2f}")

        self.put_event()