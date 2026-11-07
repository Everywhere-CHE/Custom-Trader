"""弧形底策略（Rounded Bottom Strategy）。

识别价格走势中的弧形底（U 型底）形态，在突破确认时入场做多。
"""

import numpy as np

from app.core.object import BarData, TickData
from app.core.strategy.array_manager import ArrayManager
from app.core.strategy.bar_generator import BarGenerator
from app.core.strategy.template import StrategyTemplate


class RoundedBottomStrategy(StrategyTemplate):
    """弧形底识别策略。

    通过二次拟合检测价格走势中的弧形底形态，配合成交量确认，
    在突破颈线时入场做多。适合有明显的底部整理形态的行情。
    """

    author = "custom-trader"
    display_name = "弧形底"
    description = (
        "弧形底策略：识别价格走势中的 U 型底部形态，"
        "在突破确认时入场做多。"
        "适用于横盘整理后的突破行情。"
    )

    window: int = 60
    atr_period: int = 14
    risk_per_trade: float = 0.01
    fixed_size: int = 1
    history_days: int = 30            # 初始化加载的历史数据天数

    atr_value: float = 0.0
    pattern_detected: bool = False

    parameters = ["window", "atr_period", "risk_per_trade", "fixed_size", "history_days"]
    variables = ["atr_value", "pattern_detected"]
    param_descriptions = {
        "window": "形态识别窗口（K线根数）",
        "atr_period": "ATR 计算周期",
        "risk_per_trade": "每笔交易风险比例（占账户权益）",
        "fixed_size": "每次开仓手数（0 时按 risk_per_trade 计算）",
        "history_days": "初始化加载的历史数据天数（如 30 = 加载30天1分钟K线预热）",
    }
    variable_descriptions = {
        "atr_value": "当前 ATR 值",
        "pattern_detected": "是否检测到弧形底形态",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=max(self.window + 5, 65))
        self.bg = BarGenerator(self.on_bar)
        self._entry_price = 0.0
        self._stop_price = 0.0

    def on_init(self):
        self.write_log("strategy on_init")
        # 重置内部状态，支持停止后重新初始化
        self.am = ArrayManager(size=max(self.window + 5, 65))
        self._entry_price = 0.0
        self._stop_price = 0.0
        self.atr_value = 0.0
        self.pattern_detected = False
        self.pos = 0.0
        self.load_bar(self.history_days)

    def on_start(self):
        self.write_log("strategy on_start")

    def on_stop(self):
        self.write_log("strategy on_stop")

    def on_tick(self, tick: TickData):
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData):
        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        # 计算 ATR
        self.atr_value = am.atr(self.atr_period)

        # 检查弧形底形态
        self.pattern_detected = self._check_rounded_bottom()

        price = bar.close_price
        pricetick = self._get_pricetick()

        # 开仓条件
        if self.pattern_detected and self.pos == 0:
            # 突破确认：价格创近期新高
            recent_high = max(am.high[-5:]) if am.size >= 5 else price
            if price >= recent_high:
                buy_price = price + 5 * pricetick
                size = self.fixed_size or 1
                self.buy(buy_price, size)
                self._entry_price = price
                self._stop_price = price - 2 * self.atr_value
                self.write_log(
                    f"弧形底突破做多: price={price}, "
                    f"ATR={self.atr_value:.2f}, stop={self._stop_price:.2f}"
                )

        # 持仓管理
        if self.pos > 0 and self._entry_price > 0:
            # ATR 止损
            if price <= self._stop_price:
                self.sell(price - 5 * pricetick, abs(self.pos))
                self.write_log(f"止损触发: price={price}, stop={self._stop_price:.2f}")
                self._entry_price = 0
                return

            # 移动止盈（15% 盈利后启动）
            profit_pct = (price - self._entry_price) / self._entry_price
            if profit_pct >= 0.15:
                new_stop = price * 0.9
                if new_stop > self._stop_price:
                    self._stop_price = new_stop
                    self.write_log(f"移动止盈更新: stop={new_stop:.2f}")

        elif self.pos == 0:
            self._entry_price = 0.0
            self._stop_price = 0.0

        self.put_event()

    def _check_rounded_bottom(self) -> bool:
        """检测弧形底形态（简化版）。

        使用二次拟合检测 U 型底部，配合波动收缩和成交量恢复验证。
        """
        if self.am.size < self.window:
            return False

        # 取最近 window 根 K 线的收盘价
        prices = self.am.close[-self.window:]
        prices_np = np.array(prices, dtype=float)
        if len(prices_np) < self.window:
            return False

        # 检查是否有异常值（全零或 NAN）
        if np.any(np.isnan(prices_np)) or np.all(prices_np == 0):
            return False

        x = np.arange(self.window, dtype=float)
        # 二次拟合
        coeffs = np.polyfit(x, prices_np, 2)
        a, b, c = coeffs

        # 开口向上（a > 0 表示 U 型）
        if a <= 0:
            return False

        # 左侧跌幅检验（前 30 根有下跌）
        left_return = prices_np[30] / prices_np[0] - 1
        if left_return > -0.05:
            return False

        # 波动收缩（后半段波动小于前半段）
        if len(prices_np) >= 40:
            vol1 = np.std(prices_np[:20])
            vol2 = np.std(prices_np[20:40])
            if vol2 > vol1 * 1.5:
                return False

        # 成交量恢复（右侧成交量 > 左侧）
        if self.am.size >= self.window:
            vol_left = float(np.mean(self.am.volume[-self.window:-self.window // 2]))
            vol_right = float(np.mean(self.am.volume[-self.window // 2:]))
            if vol_right < vol_left * 0.8:
                return False

        # 突破确认（价格创新高）
        if len(prices_np) >= 5:
            recent_max = max(prices_np[:-5])
            if prices_np[-1] <= recent_max:
                return False

        return True

    def _get_pricetick(self) -> float:
        """从合约获取 tick size。"""
        engine = self.strategy_engine
        if hasattr(engine, 'main_engine') and engine.main_engine:
            oms = getattr(engine.main_engine, 'oms', None)
            if oms:
                contract = oms.get_contract(self.vt_symbol)
                if contract:
                    return contract.pricetick
        if hasattr(engine, 'pricetick'):
            return engine.pricetick
        return 0.01