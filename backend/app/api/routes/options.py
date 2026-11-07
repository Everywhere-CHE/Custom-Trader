"""期权相关端点。

- 数据同步（品种 / 合约目录 / 指定标的详情）为后台任务模式，
  状态经 ``GET /options/sync/status`` 轮询；
- 期权链（T 型报价）读本地合约目录 + bars 尾部，缺行情时按
  ``refresh=1`` 惰性补拉该到期片；
- 组合回测复用 :mod:`app.services.backtest_manager` 的异步任务
  框架（kind="option_backtest"），前端轮询 ``GET /backtest/{id}``。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.constant import Exchange, Interval
from ...core.engine.main_engine import MainEngine
from ...core.strategy.option_backtesting import OptionBacktestingEngine
from ...core.strategy.option_strategies import (
    OPTION_STRATEGY_CLASSES,
    get_option_strategy_class,
)
from ...services.optimizer import (
    METRICS,
    robust_optimize,
    run_option_once,
    walk_forward,
)
from ...db.data_engine import DataEngine
from ...db.models import OptionContractModel, OptionVarietyModel
from ...db.session import create_session
from ...services.backtest_manager import backtest_manager
from ...services.option_sync import (
    EXCHANGE_BY_CODE,
    ensure_option_bars,
    make_client,
    make_rate_box,
    option_sync_manager,
    underlying_variety_id,
)
from ..deps import get_main_engine
from ..schemas import OptionBacktestBody, OptionOptimizeBody, OptionSyncBody

router = APIRouter(prefix="/options", tags=["options"])


def _get_data_engine(main_engine: MainEngine) -> DataEngine:
    engine = main_engine.get_engine("data")
    if not isinstance(engine, DataEngine):
        raise HTTPException(503, "DataEngine not available")
    return engine


@router.get("/varieties")
def list_varieties() -> dict:
    """期权品种资料（本地快照）。"""
    session = create_session()
    try:
        rows = (
            session.query(OptionVarietyModel)
            .order_by(OptionVarietyModel.exchange_code)
            .all()
        )
        return {
            "items": [
                {
                    "variety_code": r.variety_code,
                    "name": r.name,
                    "exchange_code": r.exchange_code,
                    "contract_multiplier": r.contract_multiplier,
                    "transaction_fee": r.transaction_fee,
                    "settlement_type": r.settlement_type,
                }
                for r in rows
            ]
        }
    finally:
        session.close()


@router.get("/underlyings")
def list_underlyings() -> dict:
    """可交易标的清单（ETF 按 6 位代码、股指按品种代码）。"""
    from sqlalchemy import func

    session = create_session()
    try:
        rows = (
            session.query(
                OptionContractModel.variety_code,
                OptionContractModel.exchange_code,
                func.count(OptionContractModel.thscode),
            )
            .filter(OptionContractModel.option_type.isnot(None))
            .group_by(
                OptionContractModel.variety_code,
                OptionContractModel.exchange_code,
            )
            .all()
        )
        counts = {
            (variety, exchange): count for variety, exchange, count in rows
        }
        vrows = session.query(OptionVarietyModel).all()
        names = {
            (v.variety_code, v.exchange_code): v.name for v in vrows
        }
    finally:
        session.close()

    items = []
    for (variety, exchange), count in sorted(
        counts.items(), key=lambda kv: (kv[0][1], kv[0][0])
    ):
        # ETF 品种代码 = 标的 + "O"（510050O → 510050）；
        # CFFEX/商品品种代码本身即标识（HO/IO/MO 不截断）
        underlying = (
            variety[:-1]
            if variety.endswith("O") and variety[:-1].isdigit()
            else variety
        )
        items.append(
            {
                "underlying": underlying,
                "variety_code": variety,
                "exchange_code": exchange,
                "name": names.get((variety, exchange), variety),
                "contract_count": count,
            }
        )
    return {"items": items}


@router.post("/sync")
def start_sync(body: OptionSyncBody) -> dict:
    """启动后台同步（varieties/contracts/details/all）。"""
    ok = option_sync_manager.start(body.scope, body.underlying)
    if not ok:
        raise HTTPException(
            409, "已有同步任务在运行中，或 scope 非法"
        )
    return {"ok": True, "status": option_sync_manager.status()}


@router.get("/sync/status")
def sync_status() -> dict:
    return option_sync_manager.status()


@router.get("/expiries")
def list_expiries(
    underlying: str = Query(..., description="标的：510050 或 IO"),
) -> dict:
    """某标的的到期日清单（含各月合约数）。"""
    vid = underlying_variety_id(underlying)
    session = create_session()
    try:
        vrow = (
            session.query(OptionVarietyModel.exchange_code)
            .filter(OptionVarietyModel.variety_code == vid)
            .first()
        )
        if vrow is None:
            raise HTTPException(404, f"品种 {vid} 未同步，请先执行数据同步")
        rows = (
            session.query(
                OptionContractModel.last_trade_date,
                OptionContractModel.option_type,
            )
            .filter(
                OptionContractModel.variety_code == vid,
                OptionContractModel.exchange_code == vrow[0],
                OptionContractModel.last_trade_date.isnot(None),
            )
            .all()
        )
    finally:
        session.close()
    today = datetime.now().strftime("%Y-%m-%d")
    agg: dict[str, dict] = {}
    for expiry, otype in rows:
        if not expiry or expiry <= today:
            continue
        slot = agg.setdefault(expiry, {"expiry": expiry, "calls": 0, "puts": 0})
        if otype == "call":
            slot["calls"] += 1
        elif otype == "put":
            slot["puts"] += 1
    return {"items": sorted(agg.values(), key=lambda x: x["expiry"])}


@router.get("/contracts")
def list_contracts(
    underlying: str = Query(...),
    expiry: str | None = Query(None, description="yyyy-mm-dd"),
    option_type: str | None = Query(None, description="call/put"),
) -> dict:
    """某标的（可按到期日/类型过滤）的合约清单。"""
    vid = underlying_variety_id(underlying)
    session = create_session()
    try:
        vrow = (
            session.query(OptionVarietyModel.exchange_code)
            .filter(OptionVarietyModel.variety_code == vid)
            .first()
        )
        if vrow is None:
            raise HTTPException(404, f"品种 {vid} 未同步")
        q = session.query(OptionContractModel).filter(
            OptionContractModel.variety_code == vid,
            OptionContractModel.exchange_code == vrow[0],
        )
        if expiry:
            q = q.filter(OptionContractModel.last_trade_date == expiry)
        if option_type:
            q = q.filter(OptionContractModel.option_type == option_type)
        rows = q.order_by(
            OptionContractModel.last_trade_date,
            OptionContractModel.strike_price,
        ).all()
    finally:
        session.close()
    return {
        "items": [
            {
                "thscode": r.thscode,
                "name": r.name,
                "option_type": r.option_type,
                "strike_price": r.strike_price,
                "underlying_code": r.underlying_code,
                "list_date": r.list_date,
                "last_trade_date": r.last_trade_date,
                "trade_amount": r.trade_amount,
                "margin_rate": r.margin_rate,
                "details_fetched": r.details_fetched,
            }
            for r in rows
        ]
    }


@router.get("/chain")
def get_chain(
    underlying: str = Query(...),
    expiry: str = Query(..., description="到期日 yyyy-mm-dd"),
    refresh: bool = Query(True, description="缺行情时自动补拉日线"),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """T 型报价：按行权价配对的认购/认沽截面（收盘价 + IV + Greeks）。"""
    data_engine = _get_data_engine(main_engine)
    vid = underlying_variety_id(underlying)
    session = create_session()
    try:
        vrow = (
            session.query(OptionVarietyModel)
            .filter(OptionVarietyModel.variety_code == vid)
            .first()
        )
        if vrow is None:
            raise HTTPException(404, f"品种 {vid} 未同步，请先执行数据同步")
        contracts = (
            session.query(OptionContractModel)
            .filter(
                OptionContractModel.variety_code == vid,
                OptionContractModel.exchange_code == vrow.exchange_code,
                OptionContractModel.last_trade_date == expiry,
            )
            .all()
        )
    finally:
        session.close()
    if not contracts:
        raise HTTPException(404, "该到期日无合约")

    exchange = EXCHANGE_BY_CODE.get(vrow.exchange_code, Exchange.SSE)
    ul_code = next(
        (c.underlying_code for c in contracts if c.underlying_code), None
    )

    # 标的收盘（近 10 日）
    from ...services.option_sync import ensure_underlying_bars

    now = datetime.now()
    ul_bars = ensure_underlying_bars(
        data_engine,
        underlying,
        vrow.exchange_code,
        ul_code,
        now - timedelta(days=21),
        now,
    )
    u_close = ul_bars[-1].close_price if ul_bars else 0.0
    u_prev = ul_bars[-2].close_price if len(ul_bars) > 1 else 0.0

    # 逐合约取尾部行情（缺则补拉近 14 日）
    client = None
    rate_box = None
    rows: dict[float, dict] = {}
    fetched = 0
    try:
        for c in contracts:
            tail = data_engine.load_bars_tail(
                    c.thscode, exchange, Interval.DAILY, limit=5
                )
            if not tail and refresh:
                if client is None:
                    from ...services.option_sync import load_api_key

                    api_key = load_api_key()
                    if not api_key:
                        raise HTTPException(
                            503, "未配置 FUYAO API Key（设置页 → 数据源）"
                        )
                    client = make_client(api_key)
                    rate_box = make_rate_box()
                start = (now - timedelta(days=14)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                ensure_option_bars(
                    data_engine,
                    c.thscode,
                    start,
                    now,
                    client=client,
                    rate_box=rate_box,
                )
                tail = data_engine.load_bars_tail(
                    c.thscode, exchange, Interval.DAILY, limit=5
                )
                fetched += 1
            last = tail[-1] if tail else None
            prev = tail[-2] if len(tail) > 1 else None
            side = rows.setdefault(
                c.strike_price or 0.0, {"strike": c.strike_price or 0.0}
            )
            quote = {
                "thscode": c.thscode,
                "name": c.name,
                "option_type": c.option_type,
                "price": last.close_price if last else None,
                "prev_close": prev.close_price if prev else None,
                "change_pct": (
                    (last.close_price / prev.close_price - 1)
                    if last and prev and prev.close_price
                    else None
                ),
                "volume": last.volume if last else None,
                "oi": last.open_interest if last else None,
                "iv": None,
                "delta": None,
                "list_date": c.list_date,
                "trade_amount": c.trade_amount,
            }
            side["call" if c.option_type == "call" else "put"] = quote
    finally:
        if client is not None:
            client.close()

    # IV / Greeks（以最新标的收盘 + 剩余自然日近似）
    from ...services.option_pricing import bsm_greeks, implied_vol

    r_rate = 0.02
    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d")
    t_years = max((expiry_dt - now).days, 0) / 365.0
    for row in rows.values():
        for side_name in ("call", "put"):
            q = row.get(side_name)
            if not q or not q.get("price") or u_close <= 0:
                continue
            strike = row["strike"]
            if t_years <= 0:
                q["delta"] = (
                    1.0
                    if side_name == "call" and q["price"] and u_close > strike
                    else (-1.0 if side_name == "put" and u_close < strike else 0.0)
                )
                continue
            iv = implied_vol(
                q["price"], u_close, strike, t_years, r_rate, side_name
            )
            q["iv"] = round(iv, 4) if iv else None
            g = bsm_greeks(u_close, strike, t_years, r_rate, iv or 0.25, side_name)
            q["delta"] = round(g["delta"], 4)

    items = [rows[k] for k in sorted(rows)]
    return {
        "underlying": underlying,
        "name": vrow.name,
        "expiry": expiry,
        "underlying_close": u_close,
        "underlying_prev_close": u_prev,
        "items": items,
        "fetched_contracts": fetched,
    }


@router.get("/iv-term")
def iv_term_structure(
    underlying: str = Query(...),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """ATM 隐含波动率期限结构（仅本地缓存行情，缺数据返回 null）。

    对每个到期日取行权价最接近标的最新收盘的合约，用其最近收盘价
    反解 IV；不做网络回填，链页/回测拉过行情的月份才有值。
    """
    from ...services.option_pricing import implied_vol

    data_engine = _get_data_engine(main_engine)
    vid = underlying_variety_id(underlying)
    session = create_session()
    try:
        vrow = (
            session.query(OptionVarietyModel)
            .filter(OptionVarietyModel.variety_code == vid)
            .first()
        )
        if vrow is None:
            raise HTTPException(404, f"品种 {vid} 未同步")
        contracts = (
            session.query(OptionContractModel)
            .filter(
                OptionContractModel.variety_code == vid,
                OptionContractModel.exchange_code == vrow.exchange_code,
                OptionContractModel.option_type.isnot(None),
            )
            .all()
        )
    finally:
        session.close()

    exchange = EXCHANGE_BY_CODE.get(vrow.exchange_code, Exchange.SSE)
    now = datetime.now()
    # 标的收盘：本地读取（无则空），不触发网络
    ul_symbol = (
        underlying + (".SZ" if vrow.exchange_code == "SZSE" else ".SH")
        if underlying.isdigit()
        else None
    )
    u_close = 0.0
    if ul_symbol:
        tail = data_engine.load_bars_tail(
            ul_symbol, exchange, Interval.DAILY, limit=1
        )
        if tail:
            u_close = tail[-1].close_price
    if u_close <= 0:
        return {"underlying": underlying, "underlying_close": None, "items": []}

    by_expiry: dict[str, list] = {}
    for c in contracts:
        if c.last_trade_date and c.last_trade_date > now.strftime("%Y-%m-%d"):
            by_expiry.setdefault(c.last_trade_date, []).append(c)

    items = []
    for expiry in sorted(by_expiry):
        t_years = max(
            (datetime.strptime(expiry, "%Y-%m-%d") - now).days, 0
        ) / 365.0
        entry: dict = {"expiry": expiry, "call_iv": None, "put_iv": None}
        for otype in ("call", "put"):
            pool = [c for c in by_expiry[expiry] if c.option_type == otype]
            if not pool:
                continue
            c = min(pool, key=lambda x: abs((x.strike_price or 0) - u_close))
            tail = data_engine.load_bars_tail(
                c.thscode, exchange, Interval.DAILY, limit=1
            )
            if not tail or t_years <= 0:
                continue
            iv = implied_vol(
                tail[-1].close_price, u_close, c.strike_price or 0.0,
                t_years, 0.02, otype,
            )
            if iv:
                entry[f"{otype}_iv"] = round(iv, 4)
                entry[f"{otype}_thscode"] = c.thscode
                entry[f"{otype}_strike"] = c.strike_price
        items.append(entry)
    return {
        "underlying": underlying,
        "underlying_close": u_close,
        "items": items,
    }


@router.get("/bars")
def get_option_bars(
    thscode: str = Query(..., description="期权合约 thscode"),
    days: int = Query(60, ge=5, le=400, description="回看自然日数"),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """单合约日K（缺失时按需回填）。"""
    data_engine = _get_data_engine(main_engine)
    now = datetime.now()
    start = (now - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    bars = ensure_option_bars(data_engine, thscode, start, now)
    return {
        "thscode": thscode,
        "items": [
            {
                "date": b.datetime.strftime("%Y-%m-%d") if b.datetime else None,
                "open": b.open_price,
                "high": b.high_price,
                "low": b.low_price,
                "close": b.close_price,
                "volume": b.volume,
                "turnover": b.turnover,
            }
            for b in bars
        ],
    }


@router.post("/optimize")
def run_option_optimize(
    body: OptionOptimizeBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """期权模板参数优化（K 段稳健筛选 / Walk-Forward，复用
    /backtest/{task_id} 轮询通道，kind="option_optimize"）。"""
    cls = get_option_strategy_class(body.class_name)
    if cls is None:
        raise HTTPException(404, f"未知期权策略模板: {body.class_name}")
    if body.mode not in ("robust", "walkforward"):
        raise HTTPException(422, "mode 仅支持 robust | walkforward")
    if body.metric not in METRICS:
        raise HTTPException(
            422, f"不支持的优化目标 {body.metric!r}，可选: {', '.join(METRICS)}"
        )
    data_engine = _get_data_engine(main_engine)
    _ = data_engine  # runner 内经 main_engine 取用，此处仅预检

    cost = {
        "capital": body.capital,
        "commission": body.commission,
        "slippage": body.slippage,
        "risk_free": body.risk_free,
        "iv_fallback": body.iv_fallback,
        "margin_coef": body.margin_coef,
        "margin_limit_pct": body.margin_limit_pct,
        "strike_range_pct": body.strike_range_pct,
    }

    def _runner(progress) -> dict:  # noqa: ANN001
        if body.mode == "robust":
            return robust_optimize(
                main_engine,
                cls,
                body.underlying,
                Interval.DAILY,
                body.start,
                body.end or datetime.now(),
                grid=body.grid,
                base_setting=body.setting,
                cost=cost,
                metric=body.metric,
                k_segments=body.k_segments,
                progress=progress,
                runner=run_option_once,
            )
        return walk_forward(
            main_engine,
            cls,
            body.underlying,
            Interval.DAILY,
            body.start,
            body.end or datetime.now(),
            grid=body.grid,
            base_setting=body.setting,
            cost=cost,
            metric=body.metric,
            train_days=body.train_days,
            test_days=body.test_days,
            step_days=body.step_days,
            progress=progress,
            runner=run_option_once,
        )

    task = backtest_manager.submit_generic(
        _runner,
        kind="option_optimize",
        label=f"期权优化 {body.class_name}({body.mode})",
    )
    return task.to_dict(include_result=False)


@router.get("/backtest/templates")
def backtest_templates() -> dict:
    """内置期权组合模板（前端动态渲染参数表单）。"""
    return {
        "items": [
            {
                "class_name": cls.__name__,
                "display_name": cls.display_name or cls.__name__,
                "description": cls.description,
                "author": cls.author,
                "parameters": {
                    k: getattr(cls, k, None) for k in cls.parameters
                },
                "param_descriptions": dict(cls.param_descriptions),
                "variables": list(cls.variables),
            }
            for cls in OPTION_STRATEGY_CLASSES
        ]
    }


@router.post("/backtest")
def run_option_backtest(
    body: OptionBacktestBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """提交期权组合回测任务（前端轮询 GET /backtest/{task_id}）。"""
    cls = get_option_strategy_class(body.class_name)
    if cls is None:
        raise HTTPException(404, f"未知期权策略模板: {body.class_name}")
    data_engine = _get_data_engine(main_engine)

    def _runner(progress) -> dict:  # noqa: ANN001
        engine = OptionBacktestingEngine()
        engine.set_parameters(
            underlying=body.underlying,
            start=body.start,
            end=body.end or datetime.now(),
            capital=body.capital,
            commission=body.commission,
            slippage=body.slippage,
            risk_free=body.risk_free,
            iv_fallback=body.iv_fallback,
            margin_coef=body.margin_coef,
            margin_limit_pct=body.margin_limit_pct,
            strike_range_pct=body.strike_range_pct,
        )
        engine.add_strategy(cls, body.setting)
        progress("加载数据（标的日线/链/期权日K）…")
        if not engine.load_data(data_engine):
            raise RuntimeError(
                "无可回测数据：请先在期权页执行数据同步，"
                "并确认回测区间与标的有数据"
            )
        progress("组合生命周期回放中…")
        engine.run_backtesting()
        stats = engine.calculate_statistics()
        stats["class_name"] = body.class_name
        return {
            "statistics": stats,
            "daily_results": engine.daily_results,
            "trades": engine.get_all_trades(),
            "logs": engine.logs[-500:],
            "payoff": engine.get_payoff(),
            "legs": engine.get_legs_snapshot(),
        }

    task = backtest_manager.submit_generic(
        _runner, kind="option_backtest", label=f"期权回测 {body.class_name}"
    )
    return task.to_dict(include_result=False)
