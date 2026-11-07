"""唐奇安通道突破趋势策略（使用本地停止单，优化版）。"""

from ...object import BarData, TickData
from ..array_manager import ArrayManager
from ..bar_generator import BarGenerator
from ..template import StrategyTemplate


class DonchianStrategy(StrategyTemplate):
    """海龟交易法简化版：通过停止单在通道突破时入场。"""

    author = "custom-trader"
    display_name = "唐奇安通道突破"
    description = (
        "唐奇安通道突破策略（海龟交易法简化版）：无仓位时在入场通道"
        "（entry_window 根K线最高/最低价）上下沿挂停止单，价格突破上沿"
        "自动追多、跌破下沿自动追空；持仓后改挂出场通道（exit_window）"
        "的反向停止单作为移动止损。每根K线重挂一次停止单。"
        "经典趋势突破策略，捕捉大趋势，震荡市会被反复扫损。"
        "可选的固定止损功能：stop_loss_pct 设正值开启。"
    )

    entry_window: int = 20
    exit_window: int = 10
    atr_window: int = 14
    fixed_size: int = 1
    stop_loss_pct: float = 0.0    # 固定止损（%），0=关闭

    entry_up: float = 0.0
    entry_down: float = 0.0
    exit_up: float = 0.0
    exit_down: float = 0.0
    atr_value: float = 0.0

    parameters = ["entry_window", "exit_window", "atr_window", "fixed_size", "stop_loss_pct"]
    variables = ["entry_up", "entry_down", "exit_up", "exit_down", "atr_value"]
    param_descriptions = {
        "entry_window": "入场通道周期：突破近 N 根K线最高/最低价时开仓",
        "exit_window": "出场通道周期：跌破近 N 根K线极值时平仓（移动止损）",
        "atr_window": "ATR 波动率计算周期（辅助观察，暂不参与开平仓）",
        "fixed_size": "每次开仓手数",
        "stop_loss_pct": "固定止损百分比（如 2.0 = 亏损2%时平仓），0=关闭",
    }
    variable_descriptions = {
        "entry_up": "入场通道上沿（做多触发价）",
        "entry_down": "入场通道下沿（做空触发价）",
        "exit_up": "出场通道上沿（空单止损价）",
        "exit_down": "出场通道下沿（多单止损价）",
        "atr_value": "当前 ATR 波动率",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting) -> None:  # noqa: ANN001
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=max(self.entry_window * 2, 60))
        self.bg = BarGenerator(self.on_bar)
        self._entry_price: float = 0.0

    def _check_stop_loss(self, bar: BarData) -> bool:
        """检查固定止损，触发则平仓。返回是否已平仓。"""
        if self.pos == 0 or self.stop_loss_pct <= 0 or not self._entry_price:
            return False
        if self.pos > 0:
            loss_pct = (self._entry_price - bar.close_price) / self._entry_price * 100
            if loss_pct >= self.stop_loss_pct:
                self.sell(bar.close_price * 0.99, abs(self.pos))
                self.write_log(f"固定止损触发：亏损 {loss_pct:.2f}%")
                return True
        else:
            loss_pct = (bar.close_price - self._entry_price) / self._entry_price * 100
            if loss_pct >= self.stop_loss_pct:
                self.cover(bar.close_price * 1.01, abs(self.pos))
                self.write_log(f"固定止损触发：亏损 {loss_pct:.2f}%")
                return True
        return False

    def on_init(self) -> None:
        self.write_log("strategy on_init")
        self.load_bar(20)

    def on_start(self) -> None:
        self.write_log("strategy on_start")

    def on_stop(self) -> None:
        self.write_log("strategy on_stop")

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        # 先检查止损
        if self._check_stop_loss(bar):
            self.put_event()
            return

        # 只取消本策略在当前合约上的订单，不影响其他策略或手动交易
        self.cancel_all()

        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        self.entry_up, self.entry_down = am.donchian(self.entry_window)
        self.exit_up, self.exit_down = am.donchian(self.exit_window)
        self.atr_value = am.atr(self.atr_window)

        if not self.pos:
            self.buy(self.entry_up, self.fixed_size, stop=True)
            self.short(self.entry_down, self.fixed_size, stop=True)
        elif self.pos > 0:
            self.sell(self.exit_down, abs(self.pos), stop=True)
        else:
            self.cover(self.exit_up, abs(self.pos), stop=True)

        self.put_event()