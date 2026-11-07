"""双均线交叉策略（优化版）。"""

from ...object import BarData, TickData
from ..array_manager import ArrayManager
from ..bar_generator import BarGenerator
from ..template import StrategyTemplate


class MaCrossStrategy(StrategyTemplate):
    """快慢 SMA 的金叉 / 死叉。"""

    author = "custom-trader"
    display_name = "双均线交叉"
    description = (
        "双均线交叉策略：计算快慢两条简单移动均线（SMA）。"
        "快线上穿慢线（金叉）时买入开多，若持有空单则先平空；"
        "快线下穿慢线（死叉）时卖出开空，若持有多单则先平多。"
        "经典趋势跟随策略，适合有明显趋势的行情，震荡市中容易反复止损。"
        "可选的止损功能：stop_loss_pct 设正值开启固定比例止损，"
        "trailing_stop_pct 设正值开启移动止损。"
        "反手冷却：cooldown_bars 设正值后，开仓后该根数内的反向交叉信号将被忽略，"
        "避免震荡市中短时间内多空来回反手。"
    )

    fast_window: int = 10
    slow_window: int = 20
    fixed_size: int = 1
    stop_loss_pct: float = 0.0        # 固定止损（%），0=关闭
    trailing_stop_pct: float = 0.0    # 移动止损（%），0=关闭
    cooldown_bars: int = 5            # 开仓后反手冷却K线根数，0=关闭
    history_days: int = 30            # 初始化加载的历史数据天数

    fast_ma: float = 0.0
    slow_ma: float = 0.0

    parameters = ["fast_window", "slow_window", "fixed_size", "stop_loss_pct", "trailing_stop_pct", "cooldown_bars", "history_days"]
    variables = ["fast_ma", "slow_ma"]
    param_descriptions = {
        "fast_window": "快速均线周期（K线根数），越小对价格变化越敏感",
        "slow_window": "慢速均线周期（K线根数），须大于快线周期",
        "fixed_size": "每次开仓手数",
        "stop_loss_pct": "固定止损百分比（如 2.0 = 亏损2%时平仓），0=关闭",
        "trailing_stop_pct": "移动止损百分比（如 3.0 = 从最高价回撤3%时平仓），0=关闭",
        "cooldown_bars": "开仓后反手冷却K线根数（1分钟线场景下相当于分钟数）；冷却期内出现的反向交叉信号将被忽略，避免短时间内多空来回反手；0=关闭",
        "history_days": "初始化加载的历史数据天数（如 30 = 加载30天1分钟K线预热）",
    }
    variable_descriptions = {
        "fast_ma": "当前快速均线值",
        "slow_ma": "当前慢速均线值",
    }

    def __init__(self, strategy_engine, strategy_name, vt_symbol, setting) -> None:  # noqa: ANN001
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=max(self.slow_window + 5, 25))
        # 把已完成的 1 分钟 K线 保存到数据库，重启后无需再等 25 分钟
        data_engine = getattr(strategy_engine, 'main_engine', None)
        if data_engine:
            data_engine = data_engine.get_engine('data')
        save_bar = data_engine.save_bar if data_engine and hasattr(data_engine, 'save_bar') else None
        self.bg = BarGenerator(self.on_bar, on_save_bar=save_bar)
        self._last_fast: float | None = None
        self._last_slow: float | None = None
        self._inited = False
        self._bars_since_open: int = 0    # 距上次开仓的K线根数，用于反手冷却

        # 止损跟踪
        self._entry_price: float = 0.0
        self._highest_price: float = 0.0
        self._lowest_price: float = 0.0

    def _get_pricetick(self) -> float:
        """从合约/引擎获取 tick size，兼容实盘和回测。"""
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

    def _check_stop_loss(self, bar: BarData) -> bool:
        """检查止损条件，触发则平仓。返回是否已平仓。"""
        if self.pos == 0:
            return False
        if self.stop_loss_pct > 0 and self._entry_price:
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
        if self.trailing_stop_pct > 0:
            if self.pos > 0:
                self._highest_price = max(self._highest_price, bar.high_price)
                if self._highest_price <= 0:
                    return False
                retreat = (self._highest_price - bar.close_price) / self._highest_price * 100
                if retreat >= self.trailing_stop_pct:
                    self.sell(bar.close_price * 0.99, abs(self.pos))
                    self.write_log(f"移动止损触发：从最高 {self._highest_price:.2f} 回撤 {retreat:.2f}%")
                    return True
            else:
                self._lowest_price = min(self._lowest_price, bar.low_price)
                if self._lowest_price <= 0:
                    return False
                retreat = (bar.close_price - self._lowest_price) / self._lowest_price * 100
                if retreat >= self.trailing_stop_pct:
                    self.cover(bar.close_price * 1.01, abs(self.pos))
                    self.write_log(f"移动止损触发：从最低 {self._lowest_price:.2f} 反弹 {retreat:.2f}%")
                    return True
        return False

    def on_init(self) -> None:
        self.write_log("strategy on_init")
        # 重置内部状态，支持停止后重新初始化
        self.am = ArrayManager(size=max(self.slow_window + 5, 25))
        self._last_fast: float | None = None
        self._last_slow: float | None = None
        self._inited = False
        self._entry_price = 0.0
        self._highest_price = 0.0
        self._lowest_price = 0.0
        self._bars_since_open = 0
        self.pos = 0.0
        self.load_bar(self.history_days)

    def on_start(self) -> None:
        self.write_log("strategy on_start")

    def on_stop(self) -> None:
        self.write_log("strategy on_stop")

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        # 止损检查
        if self._check_stop_loss(bar):
            self.put_event()
            return

        fast_ma = am.sma(self.fast_window)
        slow_ma = am.sma(self.slow_window)

        self.fast_ma, self.slow_ma = fast_ma, slow_ma

        pricetick = self._get_pricetick()

        # 反手冷却计数：开仓后每根 bar 递增，用于抑制短时间内的反向开仓
        if self.cooldown_bars > 0 and self._bars_since_open < self.cooldown_bars:
            self._bars_since_open += 1
        cooldown_active = self.cooldown_bars > 0 and self._bars_since_open < self.cooldown_bars

        if not self._inited:
            self._last_fast, self._last_slow = fast_ma, slow_ma
            self._inited = True
            buy_price = bar.close_price + 5 * pricetick
            sell_price = bar.close_price - 5 * pricetick
            if fast_ma > slow_ma and self.pos < 0:
                self.cover(buy_price, abs(self.pos))
                self.buy(buy_price, self.fixed_size)
                self._bars_since_open = 0
            elif fast_ma < slow_ma and self.pos > 0:
                self.sell(sell_price, self.pos)
                self.short(sell_price, self.fixed_size)
                self._bars_since_open = 0
            self.put_event()
            return

        prev_fast, prev_slow = self._last_fast, self._last_slow
        self._last_fast, self._last_slow = fast_ma, slow_ma

        golden_cross = prev_fast <= prev_slow and fast_ma > slow_ma
        death_cross = prev_fast >= prev_slow and fast_ma < slow_ma

        buy_price = bar.close_price + 5 * pricetick
        sell_price = bar.close_price - 5 * pricetick

        if golden_cross and self.pos <= 0:
            # pos<0 为反手（平空开多），冷却期内忽略，保持空单不动；pos==0 首次开多不受冷却限制
            if not (self.pos < 0 and cooldown_active):
                if self.pos < 0:
                    self.cover(buy_price, abs(self.pos))
                self.buy(buy_price, self.fixed_size)
                self._entry_price = bar.close_price
                self._highest_price = bar.close_price
                self._bars_since_open = 0
        elif death_cross and self.pos >= 0:
            # pos>0 为反手（平多开空），冷却期内忽略，保持多单不动；pos==0 首次开空不受冷却限制
            if not (self.pos > 0 and cooldown_active):
                if self.pos > 0:
                    self.sell(sell_price, self.pos)
                self.short(sell_price, self.fixed_size)
                self._entry_price = bar.close_price
                self._lowest_price = bar.close_price
                self._bars_since_open = 0

        self.put_event()