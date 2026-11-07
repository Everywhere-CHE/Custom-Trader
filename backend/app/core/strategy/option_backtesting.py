"""期权组合回测引擎。

与 :class:`app.core.strategy.backtesting.BacktestingEngine`（单标的
价格序列回放）平行的一套**多腿组合生命周期**引擎：

- 逐交易日重放：到期结算 → 撮合前日开平仓意图 → 逐日盯市 →
  风控检查 → 记录当日结果 → 策略 ``on_day``；
- **T 日信号 T+1 收盘价成交**（与单标的引擎"下一根 bar 撮合"的
  无前视语义一致）；
- 到期结算按**内在价值现金结算**（实物交割品种同样简化；结算价
  用标的收盘价近似 VWAP/两小时均值，虚值归零不收行权费）；
- 保证金：ETF 期权按上/深交所义务仓公式（12%/7% 系数，收盘价
  近似结算价），股指等其余按 CFFEX 公式（权利金 +
  max(系数×标的 − 虚值, 50%×系数×标的)）；
- 盯市价：实际日线收盘价优先，缺 bar 时 BSM 回退（IV 取持仓腿
  已解隐波或 ``iv_fallback`` 参数）；
- 数据：标的日线与期权日K经 :mod:`app.services.option_sync`
  惰性入库；``load_data`` 预取回测窗口内、行权价在
  ``strike_range_pct`` 范围内的链，运行中按需补拉。

资金口径：``equity = capital + Σ已实现盈亏(含费用) + Σ持仓浮盈``；
保证金单独跟踪（占用不扣减 equity），``margin_limit_pct`` 触发
强平义务仓，``equity <= 0`` 判爆仓停止。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..constant import Exchange
from ..utility import CHINA_TZ
from ...services.option_pricing import bsm_greeks, bsm_price, implied_vol
from ...services.option_sync import (
    EXCHANGE_BY_CODE,
    ensure_option_bars,
    ensure_underlying_bars,
)
from .option_template import (
    ChainSlice,
    DayContext,
    LegPosition,
    OptionLegSpec,
    OptionTrade,
)

ANNUAL_DAYS = 244  # 交易日年化口径

# 交易所分组：ETF 期权（上/深交所公式）与其余（CFFEX 公式）
_ETF_EXCHANGES = {Exchange.SSE, Exchange.SZSE}

# 品种类别默认值（品种表缺失时的兜底）
_DEFAULT_MULTIPLIER = {Exchange.SSE: 10000.0, Exchange.SZSE: 10000.0}
_DEFAULT_FEE = {Exchange.SSE: 1.3, Exchange.SZSE: 1.3}
_DEFAULT_MARGIN_COEF = 0.12


@dataclass
class ChainContract:
    """链内一个合约（目录元数据 + 已加载的日K收盘表）。"""

    thscode: str
    name: str
    option_type: str  # call / put
    strike: float
    expiry: str  # last_trade_date ISO
    list_date: str
    trade_amount: float  # 合约单位
    margin_rate: float  # 小数（12% -> 0.12）
    exchange: Exchange
    closes: dict[str, float] = field(default_factory=dict)  # date -> close


@dataclass
class _OpenFlow:
    """开仓时的权利金流水（payoff 归因用）。"""

    premium: float  # 正=收权利金（空头），负=付（多头）
    fee: float


class OptionBacktestingEngine:
    """期权多腿组合回测引擎。"""

    gateway_name = "OPTION_BACKTESTING"

    def __init__(self) -> None:
        # 参数
        self.underlying: str = ""
        self.start: datetime | None = None
        self.end: datetime | None = None
        self.capital: float = 1_000_000.0
        self.commission: float = 0.0  # 每张手续费；0 = 用品种默认
        self.slippage: float = 0.0  # 每张滑点（权利金口径）
        self.risk_free: float = 0.02
        self.iv_fallback: float = 0.25
        self.margin_coef: float | None = None  # None = 按合约/默认12%
        self.margin_limit_pct: float = 0.8  # 保证金/权益超限强平；0=不启用
        self.strike_range_pct: float = 0.25  # 预取行权价范围

        # 策略
        self.strategy = None
        self.strategy_class = None

        # 数据
        self.data_engine = None
        self.exchange: Exchange = Exchange.SSE
        self.variety: dict = {}
        self.multiplier: float = 10000.0
        self.fee_per_contract: float = 1.3
        self.underlying_name: str = ""
        self.underlying_closes: dict[str, float] = {}
        self.underlying_bars: list = []  # BarData（含预热段）
        self.trading_days: list[str] = []  # ISO 升序（回测窗口内）
        self.chain: dict[str, ChainContract] = {}
        self.chain_by_expiry: dict[str, list[ChainContract]] = {}

        # 运行时
        self.legs: dict[str, LegPosition] = {}
        self.pending_opens: list[OptionLegSpec] = []
        self.pending_closes: list[str] = []
        self.close_all_flag: bool = False
        self.trades: list[OptionTrade] = []
        self.logs: list[str] = []
        self.daily_results: list[dict] = []
        self.realized_sum: float = 0.0
        self.equity: float = self.capital
        self.margin_used: float = 0.0
        self.premium_paid: float = 0.0
        self.premium_received: float = 0.0
        self.total_commission: float = 0.0
        self._prev_commission: float = 0.0
        self.open_flows: dict[str, _OpenFlow] = {}
        self.busted: bool = False
        self._iv_cache: dict[str, float] = {}
        self._current_day: str = ""

    # ------------------------------------------------------------------
    # 参数与策略
    # ------------------------------------------------------------------

    def set_parameters(
        self,
        underlying: str,
        start: str | datetime,
        end: str | datetime,
        capital: float = 1_000_000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        risk_free: float = 0.02,
        iv_fallback: float = 0.25,
        margin_coef: float | None = None,
        margin_limit_pct: float = 0.8,
        strike_range_pct: float = 0.25,
    ) -> None:
        self.underlying = underlying
        self.start = self._parse_date(start)
        self.end = self._parse_date(end)
        self.capital = float(capital)
        self.equity = self.capital
        self.commission = float(commission)
        self.slippage = float(slippage)
        self.risk_free = float(risk_free)
        self.iv_fallback = float(iv_fallback)
        self.margin_coef = (
            None if margin_coef in (None, 0) else float(margin_coef)
        )
        self.margin_limit_pct = float(margin_limit_pct)
        self.strike_range_pct = float(strike_range_pct)

    def add_strategy(self, strategy_class, setting: dict) -> None:  # noqa: ANN001
        self.strategy_class = strategy_class
        self.strategy = strategy_class(self, strategy_class.__name__, setting)

    @staticmethod
    def _parse_date(value) -> datetime:  # noqa: ANN001
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")

    def output(self, msg: str) -> None:
        now = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        self.logs.append(f"{now}\t{msg}")

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def load_data(self, data_engine) -> bool:  # noqa: ANN001
        """加载标的日线 + 链目录 + 预取窗口内期权日K。返回是否有数据。"""
        from ...db.models import OptionContractModel, OptionVarietyModel
        from ...db.session import create_session
        from ...services.option_sync import (
            load_api_key,
            make_client,
            make_rate_box,
        )

        self.data_engine = data_engine

        # 品种标识：数字标的 → 510050O；CFFEX 品种 → IO/MO/HO
        vid = (
            self.underlying
            if not self.underlying.isdigit()
            else self.underlying + "O"
        )
        session = create_session()
        try:
            variety_row = (
                session.query(OptionVarietyModel)
                .filter(OptionVarietyModel.variety_code == vid)
                .first()
            )
            if variety_row is None:
                contract_rows = []
            else:
                # 同品种代码跨交易所会冲突（IO=铁矿石/沪深300），
                # 必须按交易所一并过滤
                contract_rows = (
                    session.query(OptionContractModel)
                    .filter(
                        OptionContractModel.variety_code == vid,
                        OptionContractModel.exchange_code
                        == variety_row.exchange_code,
                        OptionContractModel.option_type.isnot(None),
                        OptionContractModel.strike_price.isnot(None),
                    )
                    .all()
                )
        finally:
            session.close()

        if variety_row is None:
            self.output(f"品种 {vid} 未同步（期权页先执行数据同步）")
            return False
        if not contract_rows:
            self.output(f"品种 {vid} 无已解析合约（请执行详情同步）")
            return False

        self.exchange = EXCHANGE_BY_CODE.get(
            variety_row.exchange_code, Exchange.SSE
        )
        self.variety = {
            "transaction_fee": variety_row.transaction_fee,
            "contract_multiplier": variety_row.contract_multiplier,
            "settlement_type": variety_row.settlement_type,
            "name": variety_row.name,
        }
        self.underlying_name = variety_row.name or vid

        # 构建链索引
        for row in contract_rows:
            contract = ChainContract(
                thscode=row.thscode,
                name=row.name,
                option_type=row.option_type or "call",
                strike=float(row.strike_price or 0),
                expiry=row.last_trade_date or "",
                list_date=row.list_date or "",
                trade_amount=float(
                    row.trade_amount
                    or variety_row.contract_multiplier
                    or _DEFAULT_MULTIPLIER.get(self.exchange, 1.0)
                ),
                margin_rate=(row.margin_rate or 12.0) / 100.0,
                exchange=self.exchange,
            )
            if not contract.expiry or contract.strike <= 0:
                continue
            self.chain[contract.thscode] = contract
            self.chain_by_expiry.setdefault(contract.expiry, []).append(
                contract
            )
        for lst in self.chain_by_expiry.values():
            lst.sort(key=lambda c: (c.strike, c.option_type))

        self.multiplier = float(
            variety_row.contract_multiplier
            or _DEFAULT_MULTIPLIER.get(self.exchange, 1.0)
        )
        self.fee_per_contract = self.commission or float(
            variety_row.transaction_fee
            or _DEFAULT_FEE.get(self.exchange, 1.3)
        )

        # 标的日线（含 40 自然日预热）
        warm_start = self.start - timedelta(days=40)
        bars = ensure_underlying_bars(
            data_engine,
            self.underlying,
            variety_row.exchange_code,
            self._resolve_underlying_code(vid),
            warm_start,
            self.end,
        )
        self.underlying_bars = bars
        self.underlying_closes = {
            b.datetime.strftime("%Y-%m-%d"): b.close_price
            for b in bars
            if b.datetime and b.close_price
        }
        self.trading_days = [
            d
            for d in sorted(self.underlying_closes)
            if self.start.strftime("%Y-%m-%d")
            <= d
            <= self.end.strftime("%Y-%m-%d")
        ]
        if not self.trading_days:
            self.output(f"标的 {self.underlying} 在回测区间内无日线数据")
            return False

        # 预取链日K：窗口内到期、行权价在初始标的价格 ±range 内
        ref_price = self._underlying_close_on_or_before(self.start)
        if ref_price <= 0:
            self.output("标的在回测起点无有效价格")
            return False
        lo = ref_price * (1 - self.strike_range_pct)
        hi = ref_price * (1 + self.strike_range_pct)
        prefetch = [
            c
            for c in self.chain.values()
            if c.expiry >= self.trading_days[0] and lo <= c.strike <= hi
        ]
        if prefetch:
            api_key = load_api_key()
            if not api_key:
                self.output("未配置 FUYAO API Key，无法拉取期权日K")
                return False
            client = make_client(api_key)
            rate_box = make_rate_box()
            try:
                for i, c in enumerate(prefetch):
                    bars = ensure_option_bars(
                        data_engine,
                        c.thscode,
                        self.start,
                        self.end,
                        client=client,
                        rate_box=rate_box,
                    )
                    c.closes = {
                        b.datetime.strftime("%Y-%m-%d"): b.close_price
                        for b in bars
                        if b.datetime and b.close_price
                    }
                    if (i + 1) % 20 == 0:
                        self.output(f"期权日K预取 {i + 1}/{len(prefetch)}")
            finally:
                client.close()
        self.output(
            f"数据就绪：{self.underlying_name} "
            f"{len(self.trading_days)} 个交易日，链内合约 "
            f"{len(self.chain)} 个（预取 {len(prefetch)} 个）"
        )
        return True

    def _resolve_underlying_code(self, vid: str) -> str | None:
        """从链目录取一个标的代码（详情里的 510050 / 1B0300）。"""
        from ...db.models import OptionContractModel
        from ...db.session import create_session

        session = create_session()
        try:
            row = (
                session.query(OptionContractModel.underlying_code)
                .filter(OptionContractModel.variety_code == vid)
                .first()
            )
        finally:
            session.close()
        return row[0] if row else None

    def _underlying_close_on_or_before(self, dt: datetime) -> float:
        target = dt.strftime("%Y-%m-%d")
        days = sorted(self.underlying_closes)
        idx = bisect.bisect_right(days, target) - 1
        if idx < 0:
            return 0.0
        return self.underlying_closes[days[idx]]

    # ------------------------------------------------------------------
    # 回放
    # ------------------------------------------------------------------

    def run_backtesting(self) -> None:
        if not self.trading_days:
            raise ValueError("没有可回测的数据")
        self.strategy.on_init()
        self.strategy.trading = True

        last_day = self.trading_days[-1]
        last_close = self.underlying_closes.get(last_day, 0.0)
        day = self.trading_days[0]

        for day in self.trading_days:
            self._current_day = day
            u_close = self.underlying_closes[day]

            # 1) 到期结算（expiry <= 当日，内在价值现金结算）
            self._settle_expiries(day, u_close)

            # 2) 执行前日开平仓意图（当日收盘成交）
            self._execute_pending(day, u_close)

            # 3) 逐日盯市 + 保证金 + Greeks + 当日结果
            stats = self._mark_to_market(day, u_close)

            # 4) 保证金超限强平 / 爆仓检测
            self._check_margin_limit(day, u_close)
            if self.busted:
                self.output(f"{day} 权益归零，回测终止")
                break

            # 5) 段末：先拍 payoff 快照，再策略回调
            if day == last_day:
                self._snapshot_payoff(day, u_close)
            self._call_strategy(day, u_close)

            self.daily_results.append(stats)

        # 段末全平（按末日收盘 + 费用）
        self._close_at_end(day, self.underlying_closes.get(day, 0.0))
        final_stats = self._mark_to_market(day, self.underlying_closes.get(day, 0.0))
        if self.daily_results and self.daily_results[-1]["date"] == day:
            self.daily_results[-1] = final_stats
        else:
            self.daily_results.append(final_stats)
        self.strategy.trading = False
        self.strategy.on_stop()

    # ------------------------------------------------------------------
    # 到期结算
    # ------------------------------------------------------------------

    def _settle_expiries(self, day: str, u_close: float) -> None:
        expired = [
            t for t, leg in self.legs.items() if leg.expiry and leg.expiry <= day
        ]
        for thscode in expired:
            leg = self.legs.pop(thscode)
            if leg.option_type == "underlying":
                continue
            if leg.option_type == "call":
                intrinsic = max(u_close - leg.strike, 0.0)
            else:
                intrinsic = max(leg.strike - u_close, 0.0)
            self.open_flows.pop(thscode, None)
            # 虚值归零不行权不收费；实值按内在价值现金结算收行权费。
            # pnl 相对开仓价计算（与平仓同式），保证空头腿开仓时收取
            # 的权利金在结算时进入已实现盈亏
            fee = self.fee_per_contract * leg.volume if intrinsic > 0 else 0.0
            pnl = (
                (intrinsic - leg.open_price)
                * leg.direction
                * leg.volume
                * leg.trade_amount
                - fee
            )
            self.realized_sum += pnl
            self.total_commission += fee
            self.margin_used = max(self.margin_used - leg.margin, 0.0)
            self.trades.append(
                OptionTrade(
                    date=day,
                    thscode=thscode,
                    name=leg.name,
                    action="settle",
                    option_type=leg.option_type,
                    strike=leg.strike,
                    expiry=leg.expiry,
                    direction=leg.direction,
                    volume=leg.volume,
                    price=intrinsic,
                    premium=0.0,
                    fee=fee,
                    margin=0.0,
                    pnl=pnl,
                )
            )
            self.output(
                f"{day} 到期结算 {leg.name} x{leg.volume} "
                f"@内在价值{intrinsic:.4f} pnl={pnl:.2f}"
            )

    # ------------------------------------------------------------------
    # 撮合（T+1 收盘）
    # ------------------------------------------------------------------

    def _execute_pending(self, day: str, u_close: float) -> None:
        if self.close_all_flag:
            self.close_all_flag = False
            self.pending_closes = list(self.legs.keys())
        closes = self.pending_closes
        self.pending_closes = []
        for thscode in closes:
            leg = self.legs.get(thscode)
            if leg is None:
                continue
            mark = self._mark_price(leg, day, u_close)
            self._close_leg(leg, day, mark, "close")

        opens = self.pending_opens
        self.pending_opens = []
        for spec in opens:
            self._open_leg(spec, day, u_close)

    def _resolve_contract(self, spec: OptionLegSpec) -> ChainContract | None:
        if spec.thscode:
            return self.chain.get(spec.thscode)
        if not spec.expiry or spec.strike is None:
            return None
        candidates = self.chain_by_expiry.get(spec.expiry, [])
        pool = [c for c in candidates if c.option_type == spec.option_type]
        if not pool:
            return None
        exact = [c for c in pool if abs(c.strike - spec.strike) < 1e-9]
        if exact:
            return exact[0]
        return min(pool, key=lambda c: abs(c.strike - spec.strike))

    def _open_leg(self, spec: OptionLegSpec, day: str, u_close: float) -> None:
        direction = 1 if spec.action == "buy" else -1

        if spec.option_type == "underlying":
            thscode = f"UNDERLYING.{self.underlying}"
            if thscode in self.legs:
                self.output(f"{day} 标的腿已存在，忽略重复开仓")
                return
            price = u_close
            if price <= 0:
                self.output(f"{day} 开仓失败：标的无有效价格")
                return
            fee = 0.0
            premium = 0.0
            leg = LegPosition(
                thscode=thscode,
                name=self.underlying_name,
                option_type="underlying",
                strike=0.0,
                expiry="",
                direction=direction,
                volume=int(spec.volume),
                trade_amount=1.0,
                open_price=price,
                open_date=day,
                margin_style="none",
                margin_coef=0.0,
            )
            leg.last_mark = price
        else:
            contract = self._resolve_contract(spec)
            if contract is None:
                self.output(
                    f"{day} 开仓失败：链中找不到 "
                    f"{spec.option_type} {spec.strike} @ {spec.expiry}"
                )
                return
            if contract.list_date and contract.list_date > day:
                self.output(
                    f"{day} 开仓失败：{contract.name} 尚未上市"
                    f"（{contract.list_date}）"
                )
                return
            if contract.thscode in self.legs:
                self.output(f"{day} {contract.name} 已持仓，忽略重复开仓")
                return
            price = self._contract_mark(contract, day, u_close)
            if price <= 0:
                self.output(f"{day} 开仓失败：{contract.name} 无有效价格")
                return
            # 买贵卖便宜：按方向加滑点
            price = max(price + self.slippage * direction, 0.0001)
            fee = self.fee_per_contract * spec.volume
            premium = -price * spec.volume * contract.trade_amount * direction
            if direction > 0:
                self.premium_paid += -premium  # 买入：premium 为负支出额
            else:
                self.premium_received += premium  # 卖出：premium 为正收入额
            leg = LegPosition(
                thscode=contract.thscode,
                name=contract.name,
                option_type=contract.option_type,
                strike=contract.strike,
                expiry=contract.expiry,
                direction=direction,
                volume=int(spec.volume),
                trade_amount=contract.trade_amount,
                open_price=price,
                open_date=day,
                margin_style=(
                    "etf" if self.exchange in _ETF_EXCHANGES else "index"
                ),
                margin_coef=(
                    self.margin_coef
                    if self.margin_coef
                    else contract.margin_rate or _DEFAULT_MARGIN_COEF
                ),
            )
            leg.last_mark = price
            thscode = contract.thscode

        self.realized_sum -= fee
        self.total_commission += fee
        if leg.option_type != "underlying":
            self.open_flows[thscode] = _OpenFlow(premium=premium, fee=fee)
        self.legs[thscode] = leg
        self.trades.append(
            OptionTrade(
                date=day,
                thscode=thscode,
                name=leg.name,
                action="open",
                option_type=leg.option_type,
                strike=leg.strike,
                expiry=leg.expiry,
                direction=direction,
                volume=leg.volume,
                price=price,
                premium=premium,
                fee=fee,
                margin=0.0,
                pnl=0.0,
            )
        )
        if leg.option_type != "underlying":
            self.output(
                f"{day} 开仓 {'买入' if direction > 0 else '卖出'} "
                f"{leg.name} x{leg.volume} @ {price:.4f} "
                f"权利金={premium:.2f}"
            )
        else:
            self.output(
                f"{day} 开仓 {'买入' if direction > 0 else '卖出'} "
                f"标的 {self.underlying} x{leg.volume}股 @ {price:.4f}"
            )

    def _close_leg(
        self, leg: LegPosition, day: str, mark: float, action: str
    ) -> None:
        self.legs.pop(leg.thscode, None)
        self.open_flows.pop(leg.thscode, None)
        if leg.option_type == "underlying":
            fee = 0.0
            slip = 0.0
        else:
            fee = self.fee_per_contract * leg.volume
            # 平多=卖（价更低）、平空=买回（价更高）：按方向不利偏移
            slip = self.slippage * leg.direction
        mark = max(mark - slip, 0.0)
        pnl = (
            (mark - leg.open_price)
            * leg.direction
            * leg.volume
            * leg.trade_amount
            - fee
        )
        self.realized_sum += pnl
        self.total_commission += fee
        self.margin_used = max(self.margin_used - leg.margin, 0.0)
        self.trades.append(
            OptionTrade(
                date=day,
                thscode=leg.thscode,
                name=leg.name,
                action=action,
                option_type=leg.option_type,
                strike=leg.strike,
                expiry=leg.expiry,
                direction=leg.direction,
                volume=leg.volume,
                price=mark,
                premium=(
                    mark * leg.volume * leg.trade_amount * leg.direction
                    if leg.option_type != "underlying"
                    else 0.0
                ),
                fee=fee,
                margin=0.0,
                pnl=pnl,
            )
        )
        self.output(
            f"{day} {'平仓' if action == 'close' else '强平'} {leg.name} "
            f"x{leg.volume} @ {mark:.4f} pnl={pnl:.2f}"
        )

    # ------------------------------------------------------------------
    # 盯市 / 保证金 / Greeks
    # ------------------------------------------------------------------

    def _contract_mark(
        self, contract: ChainContract, day: str, u_close: float
    ) -> float:
        """合约当日 mark：实际收盘价优先，缺 bar 时 BSM 回退。"""
        close = contract.closes.get(day)
        if close and close > 0:
            return close
        if u_close <= 0:
            return 0.0
        t = self._years_to_expiry(day, contract.expiry)
        if t <= 0:
            if contract.option_type == "call":
                return max(u_close - contract.strike, 0.0)
            return max(contract.strike - u_close, 0.0)
        return bsm_price(
            u_close,
            contract.strike,
            t,
            self.risk_free,
            self.iv_fallback,
            contract.option_type,
        )

    def _mark_price(self, leg: LegPosition, day: str, u_close: float) -> float:
        if leg.option_type == "underlying":
            return u_close
        contract = self.chain.get(leg.thscode)
        if contract is None:
            return leg.last_mark
        return self._contract_mark(contract, day, u_close)

    def _years_to_expiry(self, day: str, expiry: str) -> float:
        """剩余期限（交易日口径 / 244；表外到期日按日历近似）。"""
        if not expiry:
            return 0.0
        i = bisect.bisect_left(self.trading_days, day)
        j = bisect.bisect_left(self.trading_days, expiry)
        if j < len(self.trading_days):
            n = max(j - i, 0)
        else:
            d0 = datetime.strptime(day, "%Y-%m-%d")
            d1 = datetime.strptime(expiry, "%Y-%m-%d")
            n = max((d1 - d0).days * ANNUAL_DAYS / 365.0, 0)
        return n / ANNUAL_DAYS

    def _leg_margin(self, leg: LegPosition, u_close: float) -> float:
        """义务仓维持保证金（收盘价口径近似，逐日重算）。"""
        if leg.option_type == "underlying" or leg.direction > 0:
            return 0.0
        coef = leg.margin_coef or _DEFAULT_MARGIN_COEF
        mark = leg.last_mark
        if leg.margin_style == "etf":
            # 上/深交所：(权利金 + max(12%×权利金 − 虚值, 7%×标的)) × 单位
            # 认沽再取 min(…, 行权价)；7% = 12% × 7/12
            coef2 = coef * 7.0 / 12.0
            if leg.option_type == "call":
                otm = max(leg.strike - u_close, 0.0)
                m = mark + max(coef * mark - otm, coef2 * u_close)
            else:
                otm = max(u_close - leg.strike, 0.0)
                m = min(
                    mark + max(coef * mark - otm, coef2 * u_close),
                    leg.strike,
                )
        else:
            # CFFEX：权利金 + max(系数×标的 − 虚值, 50%×系数×标的) × 乘数
            if leg.option_type == "call":
                otm = max(leg.strike - u_close, 0.0)
            else:
                otm = max(u_close - leg.strike, 0.0)
            m = mark + max(coef * u_close - otm, coef * u_close * 0.5)
        return m * leg.trade_amount * leg.volume

    def _mark_to_market(self, day: str, u_close: float) -> dict:
        unrealized = 0.0
        margin = 0.0
        delta = gamma = theta = vega = 0.0
        t_cache: dict[str, float] = {}
        prev_equity = self.equity

        for leg in self.legs.values():
            mark = self._mark_price(leg, day, u_close)
            leg.last_mark = mark
            if leg.option_type == "underlying":
                unrealized += leg.unrealized_pnl
                delta += leg.direction * leg.volume
                leg.margin = 0.0
                continue
            # 隐波：能解则解（Greeks 更真实），否则用回退值
            iv = self._iv_cache.get(leg.thscode) or self.iv_fallback
            t = t_cache.get(leg.expiry)
            if t is None:
                t = self._years_to_expiry(day, leg.expiry)
                t_cache[leg.expiry] = t
            if t > 0 and mark > 0:
                solved = implied_vol(
                    mark, u_close, leg.strike, t, self.risk_free,
                    leg.option_type,
                )
                if solved:
                    iv = solved
                    self._iv_cache[leg.thscode] = solved
            leg.iv = iv
            leg.margin = self._leg_margin(leg, u_close)
            margin += leg.margin
            unrealized += leg.unrealized_pnl
            if t > 0:
                g = bsm_greeks(
                    u_close, leg.strike, t, self.risk_free, iv,
                    leg.option_type,
                )
                unit = leg.volume * leg.trade_amount
                delta += g["delta"] * leg.direction * unit
                gamma += g["gamma"] * leg.direction * unit
                theta += g["theta"] * leg.direction * unit
                vega += g["vega"] * leg.direction * unit

        self.margin_used = margin
        self.equity = self.capital + self.realized_sum + unrealized
        daily_commission = self.total_commission - self._prev_commission
        self._prev_commission = self.total_commission
        return {
            "date": day,
            "underlying_close": u_close,
            "equity": round(self.equity, 2),
            "net_pnl": round(self.equity - prev_equity, 2),
            "commission": round(daily_commission, 2),
            "margin_used": round(margin, 2),
            "delta": round(delta, 1),
            "gamma": round(gamma, 2),
            "theta": round(theta, 2),
            "vega": round(vega, 2),
            "leg_count": len(self.legs),
        }

    def _check_margin_limit(self, day: str, u_close: float) -> None:
        if self.equity <= 0:
            self.busted = True
            return
        if self.margin_limit_pct <= 0:
            return
        if self.margin_used > self.equity * self.margin_limit_pct:
            shorts = [
                t
                for t, leg in self.legs.items()
                if leg.direction < 0 and leg.option_type != "underlying"
            ]
            self.output(
                f"{day} 保证金占用 {self.margin_used:.0f} 超权益 "
                f"{self.margin_limit_pct:.0%}，强平义务仓 {len(shorts)} 条"
            )
            for thscode in shorts:
                leg = self.legs.get(thscode)
                if leg is None:
                    continue
                mark = self._mark_price(leg, day, u_close)
                self._close_leg(leg, day, mark, "force_close")
            self._mark_to_market(day, u_close)
            if self.equity <= 0:
                self.busted = True

    # ------------------------------------------------------------------
    # 策略上下文
    # ------------------------------------------------------------------

    def _call_strategy(self, day: str, u_close: float) -> None:
        expiries = sorted(
            {
                c.expiry
                for c in self.chain.values()
                if c.expiry > day and (not c.list_date or c.list_date <= day)
            }
        )
        ctx = DayContext(
            date=day,
            underlying_close=u_close,
            underlying_name=self.underlying_name,
            expiries=expiries,
            equity=self.equity,
            margin_used=self.margin_used,
            legs=tuple(self.legs.values()),
            chain_fn=self._build_chain_slice,
            days_to_fn=lambda e: max(
                bisect.bisect_left(self.trading_days, e)
                - bisect.bisect_left(self.trading_days, day),
                0,
            ),
            open_fn=self._queue_opens,
            close_fn=self._queue_closes,
            close_all_fn=self._queue_close_all,
            load_bars_fn=self._load_underlying_tail,
        )
        self.strategy.on_day(ctx)

    def _queue_opens(self, specs: list[OptionLegSpec]) -> None:
        self.pending_opens.extend(specs)

    def _queue_closes(self, thscodes: list[str]) -> None:
        self.pending_closes.extend(thscodes)

    def _queue_close_all(self) -> None:
        self.close_all_flag = True

    def _load_underlying_tail(self, days: int) -> list:
        """给策略预热：截至当前回测日的标的日线尾部（无未来泄漏）。"""
        if days <= 0:
            return []
        day = self._current_day
        tail = []
        for b in reversed(self.underlying_bars):
            if b.datetime is None:
                continue
            if b.datetime.strftime("%Y-%m-%d") > day:
                continue
            tail.append(b)
            if len(tail) >= days:
                break
        tail.reverse()
        return tail

    def _build_chain_slice(self, expiry: str) -> ChainSlice | None:
        day = self._current_day
        u_close = self.underlying_closes.get(day, 0.0)
        if u_close <= 0:
            return None
        contracts = self.chain_by_expiry.get(expiry) or []
        t = self._years_to_expiry(day, expiry)
        slice_ = ChainSlice(expiry=expiry, days_left=0, strikes=[])
        slice_.days_left = max(
            bisect.bisect_left(self.trading_days, expiry)
            - bisect.bisect_left(self.trading_days, day),
            0,
        )
        for c in contracts:
            if c.list_date and c.list_date > day:
                continue
            if c.strike not in slice_.strikes:
                bisect.insort(slice_.strikes, c.strike)
            mark = self._contract_mark(c, day, u_close)
            if t > 0:
                g = bsm_greeks(
                    u_close, c.strike, t, self.risk_free,
                    self.iv_fallback, c.option_type,
                )
                d = g["delta"]
            elif c.option_type == "call":
                d = 1.0 if u_close > c.strike else 0.0
            else:
                d = -1.0 if u_close < c.strike else 0.0
            if c.option_type == "call":
                slice_.call_marks[c.strike] = mark
                slice_.call_delta[c.strike] = d
            else:
                slice_.put_marks[c.strike] = mark
                slice_.put_delta[c.strike] = d
        return slice_

    # ------------------------------------------------------------------
    # 段末处理
    # ------------------------------------------------------------------

    def _snapshot_payoff(self, day: str, u_close: float) -> None:
        """在段末平仓前对当前持仓拍到期损益图快照。"""
        points = []
        if u_close > 0 and self.legs:
            open_fee = sum(f.fee for f in self.open_flows.values())
            for i in range(-30, 31):
                s = u_close * (1 + i * 0.01)
                value = 0.0
                for leg in self.legs.values():
                    if leg.option_type == "underlying":
                        value += (
                            (s - leg.open_price)
                            * leg.direction
                            * leg.volume
                        )
                        continue
                    if leg.option_type == "call":
                        intrinsic = max(s - leg.strike, 0.0)
                    else:
                        intrinsic = max(leg.strike - s, 0.0)
                    value += (
                        (intrinsic - leg.open_price)
                        * leg.direction
                        * leg.volume
                        * leg.trade_amount
                    )
                points.append(
                    {"price": round(s, 6), "pnl": round(value - open_fee, 2)}
                )
        self._payoff_snapshot = {
            "underlying_close": u_close,
            "date": day,
            "points": points,
            "legs": [
                {
                    "thscode": leg.thscode,
                    "name": leg.name,
                    "option_type": leg.option_type,
                    "strike": leg.strike,
                    "expiry": leg.expiry,
                    "direction": leg.direction,
                    "volume": leg.volume,
                    "open_price": leg.open_price,
                }
                for leg in self.legs.values()
            ],
        }

    def _close_at_end(self, day: str, u_close: float) -> None:
        for thscode in list(self.legs.keys()):
            leg = self.legs.get(thscode)
            if leg is None:
                continue
            mark = self._mark_price(leg, day, u_close)
            self._close_leg(leg, day, mark, "close")

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------

    def get_all_trades(self) -> list[dict]:
        return [t.to_dict() for t in self.trades]

    def get_payoff(self) -> dict:
        return getattr(self, "_payoff_snapshot", {})

    def get_legs_snapshot(self) -> list[dict]:
        """段末平仓前的最终持仓（legs 序列化）。"""
        return self.get_payoff().get("legs", [])

    def calculate_statistics(self) -> dict:
        equity_list = [d["equity"] for d in self.daily_results]
        if not equity_list:
            return {}
        end_balance = equity_list[-1]
        total_return = (
            end_balance / self.capital - 1 if self.capital else 0.0
        )
        total_days = len(equity_list)
        annual_return = (
            total_return / total_days * ANNUAL_DAYS if total_days else 0.0
        )

        # 最大回撤
        max_dd = 0.0
        max_ddpct = 0.0
        dd_duration = 0
        cur_dd_days = 0
        peak = equity_list[0]
        for eq in equity_list:
            if eq > peak:
                peak = eq
                cur_dd_days = 0
            else:
                cur_dd_days += 1
                dd_duration = max(dd_duration, cur_dd_days)
                dd = peak - eq
                if dd > max_dd:
                    max_dd = dd
                ddp = dd / peak if peak else 0.0
                if ddp > max_ddpct:
                    max_ddpct = ddp

        daily_returns = [
            equity_list[i] / equity_list[i - 1] - 1
            for i in range(1, total_days)
            if equity_list[i - 1]
        ]
        if daily_returns:
            mean_r = sum(daily_returns) / len(daily_returns)
            std = (
                sum((r - mean_r) ** 2 for r in daily_returns)
                / (len(daily_returns) - 1)
            ) ** 0.5 if len(daily_returns) > 1 else 0.0
            sharpe = mean_r / std * (ANNUAL_DAYS**0.5) if std > 1e-12 else 0.0
        else:
            mean_r, std, sharpe = 0.0, 0.0, 0.0

        closed = [
            t.pnl
            for t in self.trades
            if t.action in ("close", "settle", "force_close")
        ]
        wins = [p for p in closed if p > 0]
        losses = [p for p in closed if p <= 0]
        win_rate = len(wins) / len(closed) if closed else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_win / gross_loss if gross_loss > 1e-9 else 0.0

        return {
            "underlying": self.underlying,
            "class_name": self.strategy_class.__name__
            if self.strategy_class
            else "",
            "start_date": self.trading_days[0] if self.trading_days else "",
            "end_date": self.trading_days[-1] if self.trading_days else "",
            "total_days": total_days,
            "profit_days": sum(
                1
                for i in range(1, total_days)
                if equity_list[i] >= equity_list[i - 1]
            ),
            "loss_days": sum(
                1
                for i in range(1, total_days)
                if equity_list[i] < equity_list[i - 1]
            ),
            "capital": self.capital,
            "end_balance": round(end_balance, 2),
            "total_return": round(total_return, 4),
            "annual_return": round(annual_return, 4),
            "max_drawdown": round(max_dd, 2),
            "max_ddpercent": round(max_ddpct, 4),
            "max_drawdown_duration": dd_duration,
            "total_net_pnl": round(end_balance - self.capital, 2),
            "total_commission": round(self.total_commission, 2),
            "total_trade_count": len(self.trades),
            "daily_return": round(mean_r, 6) if daily_returns else 0.0,
            "return_std": round(std, 6),
            "sharpe_ratio": round(sharpe, 4),
            "return_drawdown_ratio": (
                round(total_return / max_ddpct, 4) if max_ddpct > 1e-9 else 0.0
            ),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            # 期权特有
            "premium_paid": round(self.premium_paid, 2),
            "premium_received": round(self.premium_received, 2),
            "max_margin_used": round(
                max(
                    (d["margin_used"] for d in self.daily_results),
                    default=0.0,
                ),
                2,
            ),
            "iv_fallback": self.iv_fallback,
            "risk_free": self.risk_free,
            "busted": self.busted,
        }
