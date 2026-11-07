"""突破失败做空策略（Breakout Failure Short）。

当价格突破阻力位后放量，但随后跌回阻力位下方、趋势转弱时做空。
"""

from app.core.object import BarData, TickData
from app.core.strategy.array_manager import ArrayManager
from app.core.strategy.bar_generator import BarGenerator
from app.core.strategy.template import StrategyTemplate


class BreakoutFailureShort(StrategyTemplate):
    """突破失败做空策略。

    价格突破近期高点（放量确认）→ 跌回支撑位下方 → 趋势转弱 → 做空。
    适合有明显阻力位的震荡行情，趋势行情中容易错过主升浪。
    """

    author = "custom-trader"
    display_name = "突破失败做空"
    description = (
        "突破失败做空策略：当价格突破近期高点后放量确认，"
        "但随后跌回阻力位下方且趋势转弱时做空。"
        "适用于震荡行情中的假突破识别。"
    )

    lookback: int = 60
    breakout_percent: float = 0.02
    volume_multiple: float = 1.5
    atr_period: int = 14
    fixed_size: int = 1
    stop_loss_atr: float = 2.0
    take_profit_atr: float = 6.0
    history_days: int = 30            # 初始化加载的历史数据天数

    atr_value: float = 0.0
    resistance: float = 0.0
    ma20: float = 0.0

    parameters = [
        "lookback", "breakout_percent", "volume_multiple",
        "atr_period", "fixed_size", "stop_loss_atr", "take_profit_atr",
        "history_days",
    ]
    variables = ["atr_value", "resistance", "ma20"]
    param_descriptions = {
        "lookback": "阻力位回溯周期（最高价回溯根数）",
        "breakout_percent": "突破阈值（百分比，如 0.02 = 阻力位上 2%）",
        "volume_multiple": "成交量确认倍数（相对均量的倍数）",
        "atr_period": "ATR 计算周期",
        "fixed_size": "每次开仓手数",
        "stop_loss_atr": "止损倍数（如 2 = 入场价 + 2倍ATR）",
        "take_profit_atr": "止盈倍数（如 6 = 入场价 - 6倍ATR）",
        "history_days": "初始化加载的历史数据天数（如 30 = 加载30天1分钟K线预热）",
    }
    variable_descriptions = {
        "atr_value": "当前 ATR 值",
        "resistance": "当前阻力位",
        "ma20": "20 期均线",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting):
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=max(self.lookback + 5, 65))
        self.bg = BarGenerator(self.on_bar)
        self._entry_price = 0.0
        self._had_breakout = False

    def on_init(self):
        self.write_log("strategy on_init")
        # 重置内部状态，支持停止后重新初始化
        self.am = ArrayManager(size=max(self.lookback + 5, 65))
        self._entry_price = 0.0
        self._had_breakout = False
        self.atr_value = 0.0
        self.resistance = 0.0
        self.ma20 = 0.0
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

        # 计算 20 日均线
        ma20 = am.sma(20)
        self.ma20 = ma20

        # 计算阻力位（回溯期内的最高点，不含最近2根bar）
        if am.size >= self.lookback + 2:
            resistance = max(am.high[-(self.lookback + 2):-2])  # 不含当前bar和前一根bar
        elif am.size > 2:
            resistance = max(am.high[:-2])
        else:
            resistance = bar.close_price
        self.resistance = resistance

        # 成交量均量（不含当前bar）
        if am.size > 20:
            vol_ma = sum(am.volume[-21:-1]) / 20
        else:
            vol_ma = 1

        # 检查做空条件
        price = bar.close_price
        pricetick = self._get_pricetick()

        # 条件1：前一根 bar 突破阻力位（放量）
        prev_high = am.high[-2] if am.size >= 2 else 0
        prev_vol = am.volume[-2] if am.size >= 2 else 0
        had_breakout = (
            prev_high > resistance * (1 + self.breakout_percent)
            and prev_vol > vol_ma * self.volume_multiple
        )

        # 条件2：当前价格跌回阻力位下方
        failed = price < resistance

        # 条件3：趋势转弱（价格在均线下方）
        trend_weak = price < ma20

        # 做空信号
        if had_breakout and failed and trend_weak and self.pos == 0:
            sell_price = price - 5 * pricetick
            self.short(sell_price, self.fixed_size)
            self._entry_price = price
            self.write_log(
                f"突破失败做空: price={price}, resistance={resistance:.2f}, "
                f"ATR={self.atr_value:.2f}"
            )

        # 止损/止盈（仅当有持仓时）
        if self.pos < 0:
            if self._entry_price > 0:
                # 止损
                stop_price = self._entry_price + self.stop_loss_atr * self.atr_value
                if price >= stop_price:
                    self.cover(price + 5 * pricetick, abs(self.pos))
                    self.write_log(f"止损触发: price={price}, stop={stop_price:.2f}")
                    self._entry_price = 0
                    self.put_event()
                    return

                # 止盈
                take_profit = self._entry_price - self.take_profit_atr * self.atr_value
                if price <= take_profit:
                    self.cover(price + 5 * pricetick, abs(self.pos))
                    self.write_log(f"止盈触发: price={price}, tp={take_profit:.2f}")
                    self._entry_price = 0
                    self.put_event()
                    return
        elif self.pos == 0:
            self._entry_price = 0

        self.put_event()

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