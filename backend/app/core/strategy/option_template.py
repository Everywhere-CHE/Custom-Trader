"""期权组合策略模板与数据结构。

与 :mod:`app.core.strategy.template`（CtaTemplate，单标的单净持仓）
平行的一套**多腿组合**策略接口，供 :class:`app.core.strategy.
option_backtesting.OptionBacktestingEngine` 驱动：

- 策略通过 ``ctx``（:class:`DayContext`）感知每日行情与组合状态；
- 通过 ``ctx.open_legs/close_legs/close_all`` 发出腿级开平仓意图，
  **T 日信号 T+1 收盘价成交**（与现有单标的引擎"下一根 bar 撮合"
  的无前视语义一致）；
- 保证金、到期结算、逐日盯市、手续费/滑点全部由引擎承担。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OptionLegSpec:
    """一条腿的开仓/平仓意图（策略 → 引擎）。

    - ``option_type``：``call`` / ``put`` / ``underlying``（标的腿，
      如备兑中的现券，strike/expiry 留空，volume 单位为**股**）；
    - ``expiry``：ISO 日期（``yyyy-mm-dd``，合约 last_trade_date），
      通常经 ``ctx.expiries`` / ``ctx.next_expiry`` 取得；
    - ``thscode``：给出则直接按代码匹配，否则按
      (option_type, strike, expiry) 在链中解析。
    """

    action: str  # "buy" | "sell"
    option_type: str
    volume: int = 1
    strike: float | None = None
    expiry: str | None = None
    thscode: str | None = None


@dataclass
class LegPosition:
    """引擎持有的一条腿（含运行时状态）。"""

    thscode: str
    name: str
    option_type: str  # call / put / underlying
    strike: float
    expiry: str  # last_trade_date ISO；标的腿为 ""
    direction: int  # +1 long / -1 short
    volume: int  # 期权：张；标的腿：股
    trade_amount: float  # 合约单位（标的腿为 1）
    open_price: float  # 期权每张权利金 / 标的每股价格
    open_date: str
    # 运行时
    last_mark: float = 0.0
    margin: float = 0.0  # 当前占用保证金（空头期权腿）
    iv: float = 0.0  # 最新盯市隐含波动率（0 = 未知）
    # 保证金公式风格："etf"（上/深交所公式）/ "index"（CFFEX 公式）/
    # "none"（标的腿等不占用保证金）
    margin_style: str = "etf"
    margin_coef: float = 0.12  # 保证金系数（合约 margin_rate，小数）

    @property
    def unrealized_pnl(self) -> float:
        """盯市浮盈（按权利金口径，含方向）。"""
        return (
            (self.last_mark - self.open_price)
            * self.direction
            * self.volume
            * self.trade_amount
        )


@dataclass
class OptionTrade:
    """腿级成交/结算记录（开仓、平仓、到期结算）。"""

    date: str
    thscode: str
    name: str
    action: str  # open / close / settle / force_close
    option_type: str
    strike: float
    expiry: str
    direction: int  # +1 / -1（开仓方向）
    volume: int
    price: float
    premium: float  # 权利金收支（正=收入，负=支出）
    fee: float
    margin: float  # 该腿当前保证金（0 = 无）
    pnl: float  # 平仓/结算时的已实现盈亏（开仓为 0）

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "thscode": self.thscode,
            "name": self.name,
            "action": self.action,
            "option_type": self.option_type,
            "strike": self.strike,
            "expiry": self.expiry,
            "direction": self.direction,
            "volume": self.volume,
            "price": self.price,
            "premium": round(self.premium, 2),
            "fee": round(self.fee, 2),
            "margin": round(self.margin, 2),
            "pnl": round(self.pnl, 2),
        }


@dataclass
class ChainSlice:
    """某到期日的链截面（按行权价配对的双边报价）。

    ``call_marks`` / ``put_marks``：strike → 每张权利金 mark（实际
    收盘价优先，缺 bar 时为 BSM 理论价）；``call_delta`` /
    ``put_delta``：flat IV 口径的 delta（供按 delta 选档）。
    """

    expiry: str
    days_left: int
    strikes: list[float]
    call_marks: dict[float, float] = field(default_factory=dict)
    put_marks: dict[float, float] = field(default_factory=dict)
    call_delta: dict[float, float] = field(default_factory=dict)
    put_delta: dict[float, float] = field(default_factory=dict)

    def strike_by_delta(
        self, option_type: str, target: float
    ) -> float | None:
        """选 delta 绝对值最接近 target 的行权价。"""
        table = self.call_delta if option_type == "call" else self.put_delta
        if not table:
            return None
        return min(table, key=lambda k: abs(abs(table[k]) - target))

    def strike_by_otm(
        self, option_type: str, underlying: float, otm_pct: float
    ) -> float | None:
        """选虚值比例最接近 otm_pct 的行权价（负值=实值）。"""
        if not self.strikes:
            return None
        if option_type == "call":
            target = underlying * (1 + otm_pct)
            pool = [k for k in self.strikes if k >= target] or [
                max(self.strikes)
            ]
            return min(pool, key=lambda k: abs(k - target))
        target = underlying * (1 - otm_pct)
        pool = [k for k in self.strikes if k <= target] or [min(self.strikes)]
        return min(pool, key=lambda k: abs(k - target))


@dataclass
class DayContext:
    """策略每日回调的上下文（只读视图 + 下单入口）。"""

    date: str  # ISO
    underlying_close: float
    underlying_name: str
    expiries: list[str]  # 仍在交易窗口内的到期日（升序）
    equity: float
    margin_used: float
    legs: tuple[LegPosition, ...]
    # 由引擎注入的方法
    chain_fn: object = None  # callable(expiry) -> ChainSlice | None
    days_to_fn: object = None  # callable(expiry) -> int（交易日）
    open_fn: object = None  # callable(list[OptionLegSpec])
    close_fn: object = None  # callable(list[str]) 按 thscode 平腿
    close_all_fn: object = None  # callable()
    load_bars_fn: object = None  # callable(days) -> list[BarData] 标的日线预热

    def chain(self, expiry: str) -> ChainSlice | None:
        if self.chain_fn is None:
            return None
        return self.chain_fn(expiry)

    def next_expiry(self, offset: int = 0) -> str | None:
        """第 offset 个尚未到期的到期日（0=最近）。"""
        if offset < len(self.expiries):
            return self.expiries[offset]
        return None

    def days_to(self, expiry: str) -> int:
        if self.days_to_fn is None:
            return 0
        return self.days_to_fn(expiry)

    def open_legs(self, specs: list[OptionLegSpec]) -> None:
        if self.open_fn is not None:
            self.open_fn(specs)

    def close_legs(self, thscodes: list[str]) -> None:
        if self.close_fn is not None:
            self.close_fn(thscodes)

    def close_all(self) -> None:
        if self.close_all_fn is not None:
            self.close_all_fn()

    def load_underlying_bars(self, days: int) -> list:
        if self.load_bars_fn is None:
            return []
        return self.load_bars_fn(days)


class OptionStrategyTemplate:
    """期权组合策略基类（声明模式与 CtaTemplate 一致）。

    子类声明 ``parameters`` / ``variables`` / ``param_descriptions``，
    实现 ``on_day(ctx)``；开平仓意图在**次日收盘**成交。
    """

    author: str = ""
    display_name: str = ""
    description: str = ""
    parameters: list[str] = []
    variables: list[str] = []
    param_descriptions: dict[str, str] = {}
    variable_descriptions: dict[str, str] = {}

    def __init__(
        self,
        strategy_engine,  # noqa: ANN001  OptionBacktestingEngine
        strategy_name: str,
        setting: dict,
    ) -> None:
        self.strategy_engine = strategy_engine
        self.strategy_name = strategy_name
        self.inited = False
        self.trading = False
        # 声明的运行时变量
        for key in self.variables:
            if not hasattr(self, key):
                setattr(self, key, 0 if key == "pos" else "")
        self.update_setting(setting)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def update_setting(self, setting: dict) -> None:
        """只应用 parameters 里声明的键（与 CtaTemplate 一致）。"""
        for name in self.parameters:
            if name in setting and setting[name] is not None:
                setattr(self, name, setting[name])

    def on_init(self) -> None:
        """回测开始前（数据就绪、未进入交易日）。"""

    def on_day(self, ctx: DayContext) -> None:
        """每个交易日收盘调用（开平仓意图次日成交）。"""
        raise NotImplementedError

    def on_stop(self) -> None:
        """回测结束。"""

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def write_log(self, msg: str) -> None:
        self.strategy_engine.output(f"{self.strategy_name}: {msg}")

    def get_parameters(self) -> dict:
        return {name: getattr(self, name, None) for name in self.parameters}

    def get_variables(self) -> dict:
        return {name: getattr(self, name, None) for name in self.variables}
