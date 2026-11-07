"""内置期权组合策略模板（8 个常用组合）。

公共参数（全部模板共享，均可在回测表单覆盖）：
- ``month_offset``：到期月偏移（0=最近到期，1=次近…）
- ``lots``：每腿张数（标的腿 = lots × 合约单位 股）
- ``delta_target``：按 |delta| 选行权价（0=改用 otm_pct）
- ``otm_pct``：按虚值比例选行权价（0=平值；delta_target>0 时忽略）
- ``sma_days``：标的均线过滤（0=不过滤；方向型组合要求顺势开仓）
- ``stop_loss_pct``：组合权益回撤止损（0=不止损，如 0.15=回撤15%清仓）
- ``take_profit_pct``：组合权益止盈（0=不止盈）
- ``max_hold_days``：最长持有交易日（0=持有到期）

选档基于链截面 mark（实际收盘价优先，缺 bar 为 BSM 回退）与
flat IV 的 delta，跨产品（ETF/股指）行为一致。
"""

from __future__ import annotations

from .option_template import OptionLegSpec, OptionStrategyTemplate

_COMMON_PARAMS = [
    "month_offset",
    "lots",
    "delta_target",
    "otm_pct",
    "sma_days",
    "stop_loss_pct",
    "take_profit_pct",
    "max_hold_days",
]

_COMMON_DESCRIPTIONS = {
    "month_offset": "到期月偏移：0=最近到期，1=次近",
    "lots": "每腿张数（标的腿= lots×合约单位 股）",
    "delta_target": "按|delta|选行权价（如0.3；0=用 otm_pct）",
    "otm_pct": "虚值比例选行权价（0.03=虚值3%；0=平值）",
    "sma_days": "标的均线过滤天数（0=不过滤）",
    "stop_loss_pct": "组合回撤止损（0.15=回撤15%清仓；0=不止损）",
    "take_profit_pct": "组合止盈（0.1=盈利10%清仓；0=不止盈）",
    "max_hold_days": "最长持有交易日（0=持有到期）",
}


class OptionComboStrategy(OptionStrategyTemplate):
    """组合模板公共基类：开仓一次 + 组合级出场管理。"""

    # 公共参数默认值（类属性即默认配置，与 CtaTemplate 模式一致）
    month_offset: int = 0
    lots: int = 1
    delta_target: float = 0.0
    otm_pct: float = 0.03
    sma_days: int = 0
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    max_hold_days: int = 0

    variables = ["pos", "entry_date", "holding_days"]
    variable_descriptions = {
        "pos": "当前腿数",
        "entry_date": "开仓日",
        "holding_days": "已持有交易日数",
    }
    # 公共参数描述（子类扩展参数时用 {**_COMMON_DESCRIPTIONS, ...} 覆盖）
    param_descriptions = dict(_COMMON_DESCRIPTIONS)

    def __init__(self, strategy_engine, strategy_name, setting) -> None:  # noqa: ANN001
        self.pos: int = 0
        self.entry_date: str = ""
        self.holding_days: int = 0
        self._entry_equity: float = 0.0
        super().__init__(strategy_engine, strategy_name, setting)

    # ------------------------------------------------------------------
    # 公共工具
    # ------------------------------------------------------------------

    def _trend_ok(self, ctx, bullish: bool) -> bool:  # noqa: ANN001
        """均线过滤：bullish 要求收盘在 SMA 之上（sma_days=0 恒真）。"""
        if not self.sma_days:
            return True
        bars = ctx.load_underlying_bars(int(self.sma_days) + 2)
        closes = [b.close_price for b in bars]
        if len(closes) < self.sma_days:
            return False
        sma = sum(closes[-int(self.sma_days):]) / self.sma_days
        return closes[-1] > sma if bullish else closes[-1] < sma

    def _pick_expiry(self, ctx) -> str | None:  # noqa: ANN001
        return ctx.next_expiry(int(self.month_offset or 0))

    def _pick_strike(self, ctx, slice_, option_type: str):  # noqa: ANN001
        """选行权价：delta 优先，其次虚值比例（0=平值）。"""
        if slice_ is None:
            return None
        if self.delta_target:
            return slice_.strike_by_delta(
                option_type, abs(float(self.delta_target))
            )
        return slice_.strike_by_otm(
            option_type, ctx.underlying_close, float(self.otm_pct or 0)
        )

    def _strike_by_offset(self, slice_, base_strike: float, up: bool) -> float | None:  # noqa: ANN001
        """取链中 base_strike 之上（up）/之下的最近行权价。"""
        if slice_ is None or base_strike is None:
            return None
        pool = (
            [k for k in slice_.strikes if k > base_strike]
            if up
            else [k for k in slice_.strikes if k < base_strike]
        )
        if not pool:
            return None
        return (
            min(pool) if up else max(pool)
        )

    # ------------------------------------------------------------------
    # 公共出场
    # ------------------------------------------------------------------

    def _manage_exits(self, ctx) -> bool:  # noqa: ANN001
        """组合级出场检查；触发平仓返回 True。"""
        if not ctx.legs:
            return False
        if self.stop_loss_pct and self._entry_equity > 0:
            dd = 1 - ctx.equity / self._entry_equity
            if dd >= float(self.stop_loss_pct):
                self.write_log(
                    f"组合回撤 {dd:.1%} 触发止损清仓"
                )
                ctx.close_all()
                return True
        if self.take_profit_pct and self._entry_equity > 0:
            gain = ctx.equity / self._entry_equity - 1
            if gain >= float(self.take_profit_pct):
                self.write_log(f"组合盈利 {gain:.1%} 止盈清仓")
                ctx.close_all()
                return True
        if self.max_hold_days and self.holding_days >= int(
            self.max_hold_days
        ):
            self.write_log(f"持有 {self.holding_days} 日到期清仓")
            ctx.close_all()
            return True
        return False

    def _sync_state(self, ctx) -> None:  # noqa: ANN001
        self.pos = len(ctx.legs)
        if ctx.legs:
            if not self.entry_date:
                self.entry_date = ctx.date
                self._entry_equity = ctx.equity
                self.holding_days = 0
            else:
                self.holding_days += 1
        else:
            self.entry_date = ""
            self.holding_days = 0
            self._entry_equity = 0.0

    def on_day(self, ctx) -> None:  # noqa: ANN001
        self._sync_state(ctx)
        if self._manage_exits(ctx):
            return
        if ctx.legs:
            return
        self._try_entry(ctx)

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        raise NotImplementedError


class BullCallSpread(OptionComboStrategy):
    """牛市看涨价差：买低行权价 Call + 卖高行权价 Call（同月）。"""

    display_name = "牛市看涨价差"
    description = "看涨受限收益组合：买近月低行权价认购，卖同月更高行权价认购"
    author = "CustomTrader"
    width_pct: float = 0.03
    parameters = _COMMON_PARAMS + ["width_pct"]
    param_descriptions = {
        **_COMMON_DESCRIPTIONS,
        "width_pct": "价差宽度（标的价百分比，0.03=3%）",
    }

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        if not self._trend_ok(ctx, bullish=True):
            return
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        base = self._pick_strike(ctx, slice_, "call")
        if base is None:
            return
        upper = self._strike_by_offset(
            slice_, base * (1 + float(self.width_pct or 0.03)), up=True
        )
        if upper is None:
            return
        lots = int(self.lots or 1)
        ctx.open_legs(
            [
                OptionLegSpec("buy", "call", lots, strike=base, expiry=expiry),
                OptionLegSpec("sell", "call", lots, strike=upper, expiry=expiry),
            ]
        )
        self.write_log(
            f"开牛市价差 {expiry} 买C{base} 卖C{upper} x{lots}"
        )


class BearPutSpread(OptionComboStrategy):
    """熊市看跌价差：买高行权价 Put + 卖低行权价 Put（同月）。"""

    display_name = "熊市看跌价差"
    description = "看跌受限收益组合：买近月高行权价认沽，卖同月更低行权价认沽"
    author = "CustomTrader"
    width_pct: float = 0.03
    parameters = _COMMON_PARAMS + ["width_pct"]
    param_descriptions = {
        **_COMMON_DESCRIPTIONS,
        "width_pct": "价差宽度（标的价百分比，0.03=3%）",
    }

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        if not self._trend_ok(ctx, bullish=False):
            return
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        base = self._pick_strike(ctx, slice_, "put")
        if base is None:
            return
        lower = self._strike_by_offset(
            slice_, base * (1 - float(self.width_pct or 0.03)), up=False
        )
        if lower is None:
            return
        lots = int(self.lots or 1)
        ctx.open_legs(
            [
                OptionLegSpec("buy", "put", lots, strike=base, expiry=expiry),
                OptionLegSpec("sell", "put", lots, strike=lower, expiry=expiry),
            ]
        )
        self.write_log(
            f"开熊市价差 {expiry} 买P{base} 卖P{lower} x{lots}"
        )


class IronCondor(OptionComboStrategy):
    """铁鹰：卖虚值 Put/Call + 各买一档更远保护（震荡市收租）。"""

    display_name = "铁鹰组合"
    description = "卖虚值认沽+虚值认购，各买一档更远行权价作保护，赚时间价值"
    author = "CustomTrader"
    wing_pct: float = 0.05
    parameters = _COMMON_PARAMS + ["wing_pct"]
    param_descriptions = {
        **_COMMON_DESCRIPTIONS,
        "wing_pct": "保护翼宽度（标的价百分比，0.05=5%）",
    }

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        put_sell = self._pick_strike(ctx, slice_, "put")
        call_sell = self._pick_strike(ctx, slice_, "call")
        if put_sell is None or call_sell is None:
            return
        wing = float(self.wing_pct or 0.05)
        put_buy = self._strike_by_offset(
            slice_, put_sell * (1 - wing), up=False
        )
        call_buy = self._strike_by_offset(
            slice_, call_sell * (1 + wing), up=True
        )
        if put_buy is None or call_buy is None:
            return
        lots = int(self.lots or 1)
        ctx.open_legs(
            [
                OptionLegSpec("sell", "put", lots, strike=put_sell, expiry=expiry),
                OptionLegSpec("buy", "put", lots, strike=put_buy, expiry=expiry),
                OptionLegSpec("sell", "call", lots, strike=call_sell, expiry=expiry),
                OptionLegSpec("buy", "call", lots, strike=call_buy, expiry=expiry),
            ]
        )
        self.write_log(
            f"开铁鹰 {expiry} 卖P{put_sell}/买P{put_buy} "
            f"卖C{call_sell}/买C{call_buy} x{lots}"
        )


class LongStraddle(OptionComboStrategy):
    """买入跨式：买平值 Call + 平值 Put（赌波动）。"""

    display_name = "买入跨式"
    description = "买平值认购+平值认沽，标的大涨大跌均获利，亏时间价值"
    author = "CustomTrader"
    parameters = _COMMON_PARAMS

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        strike = slice_.strike_by_otm(
            "call", ctx.underlying_close, 0.0
        )
        if strike is None:
            return
        lots = int(self.lots or 1)
        ctx.open_legs(
            [
                OptionLegSpec("buy", "call", lots, strike=strike, expiry=expiry),
                OptionLegSpec("buy", "put", lots, strike=strike, expiry=expiry),
            ]
        )
        self.write_log(f"买跨式 {expiry} C{strike}/P{strike} x{lots}")


class ShortStrangle(OptionComboStrategy):
    """卖出宽跨式：卖虚值 Call + 虚值 Put（收租，注意保证金）。"""

    display_name = "卖出宽跨式"
    description = "卖虚值认购+虚值认沽收时间价值；裸卖注意保证金占用"
    author = "CustomTrader"
    parameters = _COMMON_PARAMS

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        call_k = self._pick_strike(ctx, slice_, "call")
        put_k = self._pick_strike(ctx, slice_, "put")
        if call_k is None or put_k is None:
            return
        lots = int(self.lots or 1)
        ctx.open_legs(
            [
                OptionLegSpec("sell", "call", lots, strike=call_k, expiry=expiry),
                OptionLegSpec("sell", "put", lots, strike=put_k, expiry=expiry),
            ]
        )
        self.write_log(f"卖宽跨 {expiry} 卖C{call_k}/卖P{put_k} x{lots}")


class CoveredCall(OptionComboStrategy):
    """备兑开仓：持有标的 + 卖虚值 Call（增强收益）。"""

    display_name = "备兑开仓"
    description = "买入标的ETF/对应股数，卖出虚值认购收权利金（备兑）"
    author = "CustomTrader"
    parameters = _COMMON_PARAMS

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        call_k = self._pick_strike(ctx, slice_, "call")
        if call_k is None:
            return
        lots = int(self.lots or 1)
        shares = int(lots * 10000)  # 标的腿按 1:1 备兑（ETF 乘数 10000）
        ctx.open_legs(
            [
                OptionLegSpec("buy", "underlying", shares),
                OptionLegSpec("sell", "call", lots, strike=call_k, expiry=expiry),
            ]
        )
        self.write_log(
            f"备兑 {self.underlying_name} x{shares}股 + 卖C{call_k} x{lots}"
        )


class ProtectivePut(OptionComboStrategy):
    """保护性认沽：持有标的 + 买虚值 Put（下行保险）。"""

    display_name = "保护性认沽"
    description = "买入标的，同时买虚值认沽做下行保护（付保险费）"
    author = "CustomTrader"
    parameters = _COMMON_PARAMS

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        put_k = self._pick_strike(ctx, slice_, "put")
        if put_k is None:
            return
        lots = int(self.lots or 1)
        shares = int(lots * 10000)
        ctx.open_legs(
            [
                OptionLegSpec("buy", "underlying", shares),
                OptionLegSpec("buy", "put", lots, strike=put_k, expiry=expiry),
            ]
        )
        self.write_log(
            f"保护性认沽 {self.underlying_name} x{shares}股 + 买P{put_k} x{lots}"
        )


class CashSecuredPut(OptionComboStrategy):
    """卖出现金担保认沽：卖虚值 Put，愿被行权接货（看涨/震荡）。"""

    display_name = "现金担保认沽"
    description = "卖出虚值认沽收权利金，愿以行权价接货；需预留足额保证金"
    author = "CustomTrader"
    parameters = _COMMON_PARAMS

    def _try_entry(self, ctx) -> None:  # noqa: ANN001
        if not self._trend_ok(ctx, bullish=True):
            return
        expiry = self._pick_expiry(ctx)
        slice_ = ctx.chain(expiry)
        if slice_ is None:
            return
        put_k = self._pick_strike(ctx, slice_, "put")
        if put_k is None:
            return
        lots = int(self.lots or 1)
        ctx.open_legs(
            [OptionLegSpec("sell", "put", lots, strike=put_k, expiry=expiry)]
        )
        self.write_log(f"卖认沽 {expiry} P{put_k} x{lots}")


# 内置模板注册表（API /templates 返回给前端）
OPTION_STRATEGY_CLASSES: list[type[OptionComboStrategy]] = [
    BullCallSpread,
    BearPutSpread,
    IronCondor,
    LongStraddle,
    ShortStrangle,
    CoveredCall,
    ProtectivePut,
    CashSecuredPut,
]


def get_option_strategy_class(name: str) -> type[OptionComboStrategy] | None:
    for cls in OPTION_STRATEGY_CLASSES:
        if cls.__name__ == name:
            return cls
    return None
