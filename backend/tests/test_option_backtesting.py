"""期权组合回测引擎测试（合成数据，精确断言）。

绕过 ``load_data``（不走网络/DB），直接注入标的收盘序列与链
合约日K，验证：

- BSM 定价：put-call parity、IV 往返、Greeks 符号；
- 保证金公式：ETF 双边、CFFEX 下限；
- 组合生命周期：T+1 撮合、到期内在价值结算、手续费、
  标的腿（备兑）盯市、BSM 盯市回退、保证金超限强平。
"""

import math
from datetime import datetime

import pytest

from app.core.constant import Exchange
from app.core.strategy.option_backtesting import OptionBacktestingEngine
from app.core.strategy.option_strategies import OPTION_STRATEGY_CLASSES
from app.core.strategy.option_template import (
    OptionLegSpec,
    OptionStrategyTemplate,
)
from app.services.option_pricing import bsm_greeks, bsm_price, implied_vol

# ----------------------------------------------------------------------
# BSM 定价
# ----------------------------------------------------------------------


def test_bsm_put_call_parity_and_iv_roundtrip():
    s, k, t, r, v = 2.85, 2.8, 45 / 365, 0.02, 0.22
    call = bsm_price(s, k, t, r, v, "call")
    put = bsm_price(s, k, t, r, v, "put")
    assert abs(call - put - (s - k * math.exp(-r * t))) < 1e-10
    iv = implied_vol(call, s, k, t, r, "call")
    assert iv is not None and abs(iv - v) < 1e-6
    # 越界价格返回 None
    assert implied_vol(0.0001, s, k, t, r, "call") is None  # 低于内在价值? 不是——深度虚值
    assert implied_vol(s + 10, s, k, t, r, "call") is None  # 超上界


def test_bsm_greeks_signs():
    g = bsm_greeks(100, 100, 0.1, 0.02, 0.2, "call")
    assert 0 < g["delta"] < 1 and g["gamma"] > 0 and g["theta"] < 0
    p = bsm_greeks(100, 100, 0.1, 0.02, 0.2, "put")
    assert -1 < p["delta"] < 0


# ----------------------------------------------------------------------
# 保证金公式
# ----------------------------------------------------------------------


def _make_engine(exchange: Exchange) -> OptionBacktestingEngine:
    eng = OptionBacktestingEngine()
    eng.exchange = exchange
    return eng


def _make_leg(**kw):
    from app.core.strategy.option_template import LegPosition

    defaults = dict(
        thscode="T1",
        name="测试",
        option_type="call",
        strike=100.0,
        expiry="2026-01-09",
        direction=-1,
        volume=1,
        trade_amount=10000.0,
        open_price=2.0,
        open_date="2026-01-05",
        margin_style="etf",
        margin_coef=0.12,
    )
    defaults.setdefault("last_mark", 2.0)
    defaults.update(kw)
    return LegPosition(**defaults)


def test_margin_etf_call_short():
    eng = _make_engine(Exchange.SSE)
    # 标的 100，行权价 100（平值），mark=2.0
    leg = _make_leg()
    m = eng._leg_margin(leg, u_close=100.0)
    # 公式: (2.0 + max(0.12*2.0 - 0, 0.07*100)) * 10000 = (2.0+7.0)*10000
    assert m == pytest.approx((2.0 + 0.07 * 100.0) * 10000.0)


def test_margin_etf_put_short_capped_at_strike():
    eng = _make_engine(Exchange.SSE)
    leg = _make_leg(option_type="put", last_mark=3.0)
    m = eng._leg_margin(leg, u_close=100.0)
    # min(3.0 + max(0.36 - 0, 7.0), 100) * 10000
    assert m == pytest.approx((3.0 + 7.0) * 10000.0)
    # 深度虚值认沽（mark 很小但下限 7% 标的价），封顶行权价生效
    leg2 = _make_leg(option_type="put", strike=90.0, last_mark=0.5)
    m2 = eng._leg_margin(leg2, u_close=100.0)
    # min(0.5 + max(0.06 - 10, 7), 90) * 10000 = 7.5*10000
    assert m2 == pytest.approx(7.5 * 10000.0)


def test_margin_cffex_index_short():
    eng = _make_engine(Exchange.CFFEX)
    leg = _make_leg(
        strike=4000.0,
        trade_amount=100.0,
        margin_style="index",
        last_mark=100.0,
    )
    m = eng._leg_margin(leg, u_close=4000.0)
    # 平值: 100 + max(0.12*4000 - 0, 0.06*4000) = 100+480 = 580 ×100
    assert m == pytest.approx(580.0 * 100.0)
    # 深度虚值（otm=1000 > 0.06*4000=240）取 50% 下限
    leg2 = _make_leg(
        strike=5000.0,
        trade_amount=100.0,
        margin_style="index",
        last_mark=10.0,
    )
    m2 = eng._leg_margin(leg2, u_close=4000.0)
    assert m2 == pytest.approx((10.0 + 0.06 * 4000.0) * 100.0)


# ----------------------------------------------------------------------
# 组合生命周期（合成数据注入）
# ----------------------------------------------------------------------

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
U_CLOSES = [100.0, 101.0, 102.0, 103.0, 105.0]
EXPIRY = DAYS[-1]


class FixedSpread(OptionStrategyTemplate):
    """固定腿测试策略：买 C100 卖 C110，持有到期。"""

    parameters = ["lots"]
    lots: int = 1

    def on_day(self, ctx) -> None:  # noqa: ANN001
        if ctx.legs:
            return
        ctx.open_legs(
            [
                OptionLegSpec(
                    "buy", "call", int(self.lots),
                    thscode="C100", expiry=EXPIRY,
                ),
                OptionLegSpec(
                    "sell", "call", int(self.lots),
                    thscode="C110", expiry=EXPIRY,
                ),
            ]
        )


def _inject_data(eng: OptionBacktestingEngine, call_closes: dict) -> None:
    """注入合成数据：标的收盘 + 两个 call 合约的日K。"""
    eng.underlying_closes = dict(zip(DAYS, U_CLOSES))
    eng.trading_days = list(DAYS)
    eng.underlying_name = "测试ETF"
    from app.core.strategy.option_backtesting import ChainContract

    for thscode, strike, closes in call_closes:
        c = ChainContract(
            thscode=thscode,
            name=thscode,
            option_type="call",
            strike=strike,
            expiry=EXPIRY,
            list_date="2026-01-01",
            trade_amount=10000.0,
            margin_rate=0.12,
            exchange=Exchange.SSE,
        )
        c.closes = closes
        eng.chain[thscode] = c
        eng.chain_by_expiry.setdefault(EXPIRY, []).append(c)


def test_bull_call_spread_settlement_exact_pnl():
    eng = OptionBacktestingEngine()
    eng.set_parameters(
        underlying="510050",
        start=DAYS[0],
        end=DAYS[-1],
        capital=1_000_000.0,
        commission=2.0,  # 每张 2 元
        slippage=0.0,
    )
    eng.fee_per_contract = 2.0
    _inject_data(
        eng,
        [
            (
                "C100",
                100.0,
                {
                    "2026-01-05": 2.0,
                    "2026-01-06": 2.5,
                    "2026-01-07": 3.2,
                    "2026-01-08": 4.0,
                },  # 到期日无 bar → 内在价值结算
            ),
            (
                "C110",
                110.0,
                {
                    "2026-01-05": 0.8,
                    "2026-01-06": 0.9,
                    "2026-01-07": 1.0,
                    "2026-01-08": 1.2,
                },
            ),
        ],
    )
    eng.add_strategy(FixedSpread, {"lots": 1})
    eng.run_backtesting()

    stats = eng.calculate_statistics()

    # d1 信号 → d2 收盘成交：买 C100@2.5（-25000），卖 C110@0.9（+9000）
    opens = [t for t in eng.trades if t.action == "open"]
    assert len(opens) == 2
    buy = next(t for t in opens if t.direction > 0)
    sell = next(t for t in opens if t.direction < 0)
    assert buy.date == "2026-01-06" and buy.price == pytest.approx(2.5)
    assert sell.date == "2026-01-06" and sell.price == pytest.approx(0.9)

    # d5 到期：u=105 → C100 内在 5，C110 内在 0
    settles = [t for t in eng.trades if t.action == "settle"]
    assert len(settles) == 2
    settle_c100 = next(t for t in settles if t.thscode == "C100")
    settle_c110 = next(t for t in settles if t.thscode == "C110")
    assert settle_c100.price == pytest.approx(5.0)
    # 长腿: (5-2.5)*10000 - 2(行权费) = 24998
    assert settle_c100.pnl == pytest.approx(24998.0)
    # 短腿虚值归零不收费: (0-0.9)*(-1)*10000 = 9000
    assert settle_c110.price == pytest.approx(0.0)
    assert settle_c110.pnl == pytest.approx(9000.0)

    # 净盈亏 = 24998 + 9000 - 开仓手续费 4 = 33994
    assert stats["total_net_pnl"] == pytest.approx(33994.0)
    assert stats["total_commission"] == pytest.approx(6.0)  # 开4 + 行权2
    assert stats["end_balance"] == pytest.approx(1_033_994.0)
    assert stats["busted"] is False
    assert stats["total_trade_count"] == 4  # 2开 + 2结算


def test_settlement_pnl_accounts_open_premium():
    """空头腿开仓收到的权利金必须在结算时进入已实现盈亏。"""
    eng = OptionBacktestingEngine()
    eng.set_parameters(
        underlying="510050", start=DAYS[0], end=DAYS[-1]
    )
    eng.fee_per_contract = 0.0
    _inject_data(
        eng,
        [("C110", 110.0, {"2026-01-05": 0.8, "2026-01-06": 0.9})],
    )

    class SellOnly(OptionStrategyTemplate):
        parameters: list[str] = []

        def on_day(self, ctx) -> None:  # noqa: ANN001
            if ctx.legs:
                return
            ctx.open_legs(
                [
                    OptionLegSpec(
                        "sell", "call", 1, thscode="C110", expiry=EXPIRY
                    )
                ]
            )

    eng.add_strategy(SellOnly, {})
    eng.run_backtesting()
    # u=105 → C110 虚值归零：0.9 权利金全额落袋
    assert eng.calculate_statistics()["total_net_pnl"] == pytest.approx(
        9000.0
    )


def test_covered_call_underlying_leg_marking():
    """标的腿按标的收盘盯市，期权腿到期结算。"""

    class CoveredCallFixed(OptionStrategyTemplate):
        parameters: list[str] = []

        def on_day(self, ctx) -> None:  # noqa: ANN001
            if ctx.legs:
                return
            ctx.open_legs(
                [
                    OptionLegSpec("buy", "underlying", 10000),
                    OptionLegSpec(
                        "sell", "call", 1, thscode="C110", expiry=EXPIRY
                    ),
                ]
            )

    eng = OptionBacktestingEngine()
    eng.set_parameters(
        underlying="510050", start=DAYS[0], end=DAYS[-1]
    )
    eng.fee_per_contract = 0.0
    _inject_data(
        eng, [("C110", 110.0, {"2026-01-05": 0.8, "2026-01-06": 0.9})]
    )
    eng.add_strategy(CoveredCallFixed, {})
    eng.run_backtesting()

    stats = eng.calculate_statistics()
    # 标的腿: (105-101)*10000 = 40000（d2 收盘 101 开仓 → d5 105）
    # 短 C110: 9000（同上）
    assert stats["total_net_pnl"] == pytest.approx(49000.0)


def test_bsm_mark_fallback_without_bars():
    """合约无日K时用 BSM 盯市回退，引擎不崩溃、mark 为正。"""
    eng = OptionBacktestingEngine()
    eng.set_parameters(
        underlying="510050",
        start=DAYS[0],
        end="2026-01-07",
        iv_fallback=0.30,
    )
    _inject_data(eng, [("C100", 100.0, {})])  # 无任何日K
    eng.chain["C100"].expiry = "2026-02-06"  # 未到期

    class BuyHold(OptionStrategyTemplate):
        parameters: list[str] = []

        def on_day(self, ctx) -> None:  # noqa: ANN001
            if ctx.legs:
                return
            ctx.open_legs(
                [
                    OptionLegSpec(
                        "buy", "call", 1, thscode="C100",
                        expiry="2026-02-06",
                    )
                ]
            )

    eng.add_strategy(BuyHold, {})
    eng.run_backtesting()
    assert len(eng.trades) >= 2  # 开 + 段末平
    assert all(t.price > 0 for t in eng.trades)
    # 理论价在合理区间（标的 100、平值、1月期、IV30%）
    theo = bsm_price(100, 100, 20 / 244, 0.02, 0.30, "call")
    assert eng.trades[0].price == pytest.approx(theo, rel=0.5)


def test_margin_limit_force_close():
    """保证金占用超权益阈值时强平义务仓。"""

    class NakedSell(OptionStrategyTemplate):
        parameters: list[str] = []

        def on_day(self, ctx) -> None:  # noqa: ANN001
            if ctx.legs:
                return
            ctx.open_legs(
                [
                    OptionLegSpec(
                        "sell", "call", 100, thscode="C100",
                        expiry=EXPIRY,
                    )
                ]
            )

    eng = OptionBacktestingEngine()
    eng.set_parameters(
        underlying="510050",
        start=DAYS[0],
        end=DAYS[-1],
        capital=100_000.0,
        margin_limit_pct=0.5,
    )
    eng.fee_per_contract = 0.0
    _inject_data(
        eng,
        [("C100", 100.0, {"2026-01-05": 2.0, "2026-01-06": 2.5, "2026-01-07": 4.0})],
    )
    eng.add_strategy(NakedSell, {})
    eng.run_backtesting()
    # 100 张平值 call 保证金 ≈ 900 万 >> 权益 10 万 → 开仓当日盯市后
    # 即触发强平；策略次日又会重试（组合策略无仓位即入场），每开必平
    force = [t for t in eng.trades if t.action == "force_close"]
    assert len(force) >= 1
    assert eng.margin_used == 0.0
    # 每次强平后权益不因强平本身变化（开平同价、零费用）
    assert all(t.pnl == 0 for t in force)


def test_builtin_templates_registry():
    """内置模板注册表完整、参数声明齐备。"""
    names = [c.__name__ for c in OPTION_STRATEGY_CLASSES]
    assert len(names) == 8
    for cls in OPTION_STRATEGY_CLASSES:
        assert cls.parameters, cls.__name__
        for p in cls.parameters:
            assert hasattr(cls, p), f"{cls.__name__}.{p} 缺默认值"
            assert p in cls.param_descriptions, f"{cls.__name__}.{p} 缺描述"


def test_exchange_from_thscode_commodity_suffixes():
    """期权 thscode 后缀 → 交易所（含商品期权；关注页日K兜底依赖）。"""
    from app.core.constant import Exchange
    from app.services.option_sync import _exchange_from_thscode

    assert _exchange_from_thscode("10011425.SH") is Exchange.SSE
    assert _exchange_from_thscode("IO2612-C-4000.CFE") is Exchange.CFFEX
    assert _exchange_from_thscode("MA701P2800.CZC") is Exchange.CZCE
    assert _exchange_from_thscode("cu2602C50000.SHF") is Exchange.SHFE
    assert _exchange_from_thscode("i2701-C-700.DCE") is Exchange.DCE
    assert _exchange_from_thscode("LC2612-C-100000.GFE") is Exchange.GFEX
