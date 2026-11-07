"""回测相关端点。

回测为**异步任务**：``POST /backtest`` 提交即返回 ``task_id``，
回测在后台线程执行（见 :mod:`app.services.backtest_manager`）；
前端轮询 ``GET /backtest/{task_id}`` 获取状态与结果。
长回测不再受 HTTP 超时限制。

``/download-data``、``/data``、``/performance`` 保持原同步语义不变。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from ...core.constant import Interval
from ...core.engine.main_engine import MainEngine
from ...core.object import HistoryRequest
from ...core.strategy.engine import StrategyEngine
from ...core.utility import CHINA_TZ
from ...core.utility import extract_vt_symbol
from ...db.data_engine import DataEngine
from ...services.backtest_manager import backtest_manager
from ..deps import get_main_engine
from ..schemas import (
    BacktestRequestBody,
    DownloadDataBody,
    GridPreviewBody,
    OptimizeBody,
)

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _get_data_engine(main_engine: MainEngine) -> DataEngine:
    engine = main_engine.get_engine("data")
    if not isinstance(engine, DataEngine):
        raise HTTPException(503, "DataEngine not available")
    return engine


@router.post("")
async def run_backtest(
    body: BacktestRequestBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """提交一个回测任务，立即返回 task_id（后台线程执行）。"""
    try:
        interval = Interval(body.interval)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    strategy_engine = main_engine.get_engine("strategy")
    if not isinstance(strategy_engine, StrategyEngine):
        raise HTTPException(503, "StrategyEngine not available")

    # 解析策略与合约列表
    class_names = body.class_names if body.class_names else ([body.class_name] if body.class_name else [])
    vt_symbols = body.vt_symbols if body.vt_symbols else ([body.vt_symbol] if body.vt_symbol else [])
    if not class_names:
        raise HTTPException(422, "请选择至少一个策略")
    if not vt_symbols:
        raise HTTPException(422, "请选择至少一个合约")

    # 校验所有策略类均存在（同步返回 422，而非延后到任务失败）
    strategy_classes: list[type] = []
    for cn in class_names:
        sc = strategy_engine.classes.get(cn)
        if not sc:
            raise HTTPException(422, f"策略类不存在: {cn}")
        strategy_classes.append(sc)

    # 校验所有合约代码
    for vs in vt_symbols:
        try:
            extract_vt_symbol(vs)
        except ValueError as e:
            raise HTTPException(422, f"无效合约: {vs}") from None

    # 提前确认 DataEngine 可用（否则任务必然失败）
    _get_data_engine(main_engine)

    task = backtest_manager.submit(
        main_engine, strategy_classes, vt_symbols, body, interval
    )
    return task.to_dict(include_result=False)


@router.get("")
def list_backtest_tasks(main_engine: MainEngine = Depends(get_main_engine)) -> dict:
    """列出所有回测任务（不含结果，仅状态）。"""
    return {"tasks": [t.to_dict(include_result=False) for t in backtest_manager.list()]}


# ----------------------------------------------------------------------
# 参数优化（稳健筛选 / Walk-Forward）
# ----------------------------------------------------------------------

@router.post("/optimize/preview-grid")
def preview_optimize_grid(body: GridPreviewBody) -> dict:
    """预览网格展开结果（组合数与样例），不执行回测。"""
    from ...services.optimizer import MAX_COMBOS, expand_grid

    try:
        combos = expand_grid(body.grid)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {
        "count": len(combos),
        "max_combos": MAX_COMBOS,
        "combos": combos[:50],
    }


@router.post("/optimize")
async def run_optimize(
    body: OptimizeBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """提交参数优化任务（mode=robust 稳健筛选 / walkforward 滚动样本外）。"""
    from ...services.optimizer import (
        METRICS,
        robust_optimize,
        walk_forward,
    )

    if body.mode not in ("robust", "walkforward"):
        raise HTTPException(422, "mode 必须为 robust 或 walkforward")
    if body.metric not in METRICS:
        raise HTTPException(422, f"metric 必须为 {'/'.join(METRICS)}")
    if not body.grid:
        raise HTTPException(422, "grid 不能为空——请提供至少一个参数的取值")
    try:
        interval = Interval(body.interval)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    strategy_engine = main_engine.get_engine("strategy")
    if not isinstance(strategy_engine, StrategyEngine):
        raise HTTPException(503, "StrategyEngine not available")
    strategy_class = strategy_engine.classes.get(body.class_name)
    if not strategy_class:
        raise HTTPException(422, f"策略类不存在: {body.class_name}")
    try:
        extract_vt_symbol(body.vt_symbol)
    except ValueError as e:
        raise HTTPException(422, f"无效合约: {body.vt_symbol}") from None
    _get_data_engine(main_engine)

    end = body.end or datetime.now()
    cost = {
        "capital": body.capital,
        "rate": body.rate,
        "slippage": body.slippage,
        "size": body.size,
        "pricetick": body.pricetick,
    }
    mode, metric = body.mode, body.metric

    def _runner(progress) -> dict:
        if mode == "robust":
            return robust_optimize(
                main_engine, strategy_class, body.vt_symbol, interval,
                body.start, end, body.grid, body.setting, cost,
                metric=metric, k_segments=body.k_segments, progress=progress,
            )
        return walk_forward(
            main_engine, strategy_class, body.vt_symbol, interval,
            body.start, end, body.grid, body.setting, cost,
            metric=metric,
            train_days=body.train_days,
            test_days=body.test_days,
            step_days=body.step_days,
            progress=progress,
        )

    task = backtest_manager.submit_generic(
        _runner, kind=mode, label=f"{body.class_name} @ {body.vt_symbol}"
    )
    return task.to_dict(include_result=False)


@router.post("/download-data")
async def download_data(
    body: DownloadDataBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    try:
        interval = Interval(body.interval)
        symbol, exchange = extract_vt_symbol(body.vt_symbol)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    gateway = main_engine.gateways.get(body.gateway_name)
    if not gateway:
        raise HTTPException(404, f"gateway not found: {body.gateway_name}")

    # FUYAO 已连接且能服务该交易所的日线时，即使所指定网关
    # （如 CTP）离线也放行 —— 历史数据自动路由到同花顺源。
    fuyao = main_engine.gateways.get("FUYAO")
    fuyao_can_serve = (
        fuyao is not None
        and fuyao.connected
        and interval is Interval.DAILY
        and exchange in fuyao.exchanges
    )
    if not gateway.connected and not fuyao_can_serve:
        raise HTTPException(409, f"gateway not connected: {body.gateway_name}")

    data_engine = _get_data_engine(main_engine)

    def _download() -> dict:
        # 仅当已有数据确实覆盖了请求区间时才复用
        # （之前的部分下载不能导致提前返回）。
        existing = data_engine.load_bars(symbol, exchange, interval, body.start, body.end)
        if existing:
            covered = (
                existing[0].datetime is not None
                and existing[-1].datetime is not None
                and existing[0].datetime <= body.start + timedelta(days=1)
                and (
                    body.end is None
                    or existing[-1].datetime >= body.end - timedelta(days=1)
                )
            )
            if covered:
                return {
                    "saved": len(existing),
                    "first": existing[0].datetime.isoformat() if existing[0].datetime else None,
                    "last": existing[-1].datetime.isoformat() if existing[-1].datetime else None,
                    "source": "database",
                    "interval": interval.value,
                }

        # 尝试通过网关获取
        req = HistoryRequest(
            symbol=symbol,
            exchange=exchange,
            start=body.start,
            end=body.end,
            interval=interval,
        )
        bars = main_engine.query_history(req, body.gateway_name)
        if bars:
            data_engine.save_bars(bars)
            return {
                "saved": len(bars),
                "first": bars[0].datetime.isoformat() if bars[0].datetime else None,
                "last": bars[-1].datetime.isoformat() if bars[-1].datetime else None,
                "source": "gateway",
                "interval": interval.value,
            }

        # 请求的周期没有数据：检查是否存在其他周期的数据
        for fallback_iv, fallback_label in [
            (Interval.HOUR, "1h"), (Interval.DAILY, "d"), (Interval.WEEKLY, "w")
        ]:
            fallback = data_engine.load_bars(symbol, exchange, fallback_iv, body.start, body.end)
            if fallback:
                return {
                    "saved": len(fallback),
                    "first": fallback[0].datetime.isoformat() if fallback[0].datetime else None,
                    "last": fallback[-1].datetime.isoformat() if fallback[-1].datetime else None,
                    "source": "database_fallback",
                    "interval": fallback_iv.value,
                    "note": f"请求的 {interval.value} 数据不可用，已返回 {fallback_label} 数据",
                }

        raise HTTPException(
            422, f"没有历史数据可用。请先连接 SIM 生成模拟数据，"
            f"或修改回测周期为 1h/d/w 使用已有数据。"
        )

    return await run_in_threadpool(_download)


@router.get("/data")
def get_data_overview(
    vt_symbol: str,
    interval: str = "1m",
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    try:
        bar_interval = Interval(interval)
        symbol, exchange = extract_vt_symbol(vt_symbol)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    data_engine = _get_data_engine(main_engine)
    bars = data_engine.load_bars(symbol, exchange, bar_interval)
    if not bars:
        return {"count": 0, "first": None, "last": None}
    return {
        "count": len(bars),
        "first": bars[0].datetime.isoformat() if bars[0].datetime else None,
        "last": bars[-1].datetime.isoformat() if bars[-1].datetime else None,
    }


@router.get("/performance")
def get_performance(
    hours: int = Query(24, description="统计最近多少小时的交易"),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """快速绩效汇总：最近 N 小时交易的盈亏。
    无需选择策略或合约 — 只统计近期交易活动。"""
    now = datetime.now(CHINA_TZ)
    since = now - timedelta(hours=hours)

    # 从数据库加载时间区间内的成交
    data_engine = _get_data_engine(main_engine)
    trades = data_engine.load_trades(start=since, end=now)

    # 构建合约查找表，用于盈亏计算
    contracts = {c.vt_symbol: c for c in main_engine.oms.get_all_contracts()}

    # 加载全部成交（而不仅是时间区间内的），以便匹配 CLOSE ↔ OPEN
    all_trades = data_engine.load_trades()
    all_trades.sort(key=lambda t: t.datetime or datetime.min, reverse=False)

    # 跟踪未平仓头寸，用于盈亏匹配
    from ...core.constant import Offset, Direction
    open_positions: dict[str, list[tuple[float, float]]] = {}
    pnl_map: dict[str, float] = {}  # vt_tradeid -> 净盈亏

    for t in all_trades:
        offset_str = t.offset.value if hasattr(t.offset, 'value') else str(t.offset)
        if offset_str not in ("OPEN", "CLOSE"):
            continue
        ct = contracts.get(t.vt_symbol)
        size = ct.size if ct else 1
        key = t.vt_symbol

        if offset_str == "OPEN":
            open_positions.setdefault(key, []).append((t.price, t.volume))
        else:
            entries = open_positions.get(key, [])
            if not entries:
                continue
            remaining_vol = t.volume
            matched_entry = None
            for i, (ep, ev) in enumerate(entries):
                if ev <= 0:
                    continue
                if matched_entry is None:
                    matched_entry = ep
                if ev >= remaining_vol:
                    entries[i] = (ep, ev - remaining_vol)
                    remaining_vol = 0
                    break
                else:
                    remaining_vol -= ev
                    entries[i] = (ep, 0)
            if matched_entry is None:
                continue
            open_positions[key] = [(ep, ev) for ep, ev in entries if ev > 0]
            if t.direction and t.direction.value == "SHORT":
                diff = t.price - matched_entry
            else:
                diff = matched_entry - t.price
            pnl = round(diff * size * t.volume, 2)
            fee = round(abs(pnl * 0.0003), 2) if pnl != 0 else 0.0
            pnl_map[t.vt_tradeid] = round(pnl - fee, 2)

    # 现在按时间区间过滤并计算统计指标
    total_pnl = 0.0
    total_fee = 0.0
    win_count = 0
    loss_count = 0
    trade_details = []
    trade_symbols: set[str] = set()

    for t in trades:
        net = pnl_map.get(t.vt_tradeid, 0.0)
        total_pnl += net
        # 仅对 CLOSE 成交计手续费（OPEN 成交在平仓时计费）
        offset_str = t.offset.value if hasattr(t.offset, 'value') else str(t.offset)
        if offset_str == "CLOSE":
            ct = contracts.get(t.vt_symbol)
            size = ct.size if ct else 1
            notional = t.price * size * t.volume
            fee = round(abs(notional * 0.0003), 2)
            total_fee += fee
        if net > 0:
            win_count += 1
        elif net < 0:
            loss_count += 1
        trade_symbols.add(t.vt_symbol)
        trade_details.append({
            "vt_symbol": t.vt_symbol,
            "direction": t.direction.value if t.direction else "",
            "price": t.price,
            "volume": t.volume,
            "pnl": net,
            "datetime": t.datetime.isoformat() if t.datetime else None,
        })

    total_trades = win_count + loss_count
    win_rate = round(win_count / total_trades * 100, 1) if total_trades > 0 else 0.0

    # 加载当前持仓，用于计算未实现盈亏
    positions = main_engine.oms.get_all_positions()
    unrealized_pnl = 0.0
    position_details = []
    for p in positions:
        if p.volume == 0:
            continue
        unrealized_pnl += p.pnl or 0.0
        position_details.append({
            "vt_symbol": p.vt_symbol,
            "direction": p.direction.value if p.direction else "",
            "volume": p.volume,
            "price": p.price,
            "pnl": p.pnl or 0.0,
        })

    total_pnl_final = round(total_pnl + unrealized_pnl, 2)

    # 计算收益率（基于账户权益）
    account_balance = 0.0
    for acc in main_engine.oms.accounts.values():
        if acc.balance:
            account_balance += acc.balance
    total_return = round(total_pnl_final / account_balance * 100, 4) if account_balance else 0.0

    return {
        "hours": hours,
        "since": since.isoformat(),
        "now": now.isoformat(),
        "total_trades": total_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "total_return": total_return,
        "realized_pnl": round(total_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": total_pnl_final,
        "total_fee": round(total_fee, 2),
        "symbols": sorted(trade_symbols),
        "trades": trade_details,
        "positions": position_details,
    }


@router.get("/{task_id}")
def get_backtest_task(
    task_id: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """查询单个回测任务的状态与结果（前端轮询）。

    声明在 ``/data``、``/performance`` 之后，避免被 ``{task_id}``
    形参路由遮蔽这两个固定子路径。
    """
    task = backtest_manager.get(task_id)
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return task.to_dict(include_result=True)
