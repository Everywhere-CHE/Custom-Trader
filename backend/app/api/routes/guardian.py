"""策略守护端点：建档、复验、退役、总览。

复验/全量复验为异步任务（复用回测任务通道），轮询
``GET /backtest/{task_id}`` 获取进度与结果。
"""

from fastapi import APIRouter, Depends, HTTPException

from ...core.constant import Interval
from ...core.engine.main_engine import MainEngine
from ...core.strategy.engine import StrategyEngine
from ...services import guardian
from ...services.backtest_manager import backtest_manager
from ..deps import get_main_engine
from ..schemas import GuardianAdoptBody

router = APIRouter(prefix="/guardian", tags=["guardian"])


def get_strategy_engine(
    main_engine: MainEngine = Depends(get_main_engine),
) -> StrategyEngine:
    engine = main_engine.get_engine("strategy")
    if not isinstance(engine, StrategyEngine):
        raise HTTPException(503, "StrategyEngine not available")
    return engine


def _cost_from_body(body: GuardianAdoptBody) -> dict:
    return {
        "capital": body.capital,
        "rate": body.rate,
        "slippage": body.slippage,
        "size": body.size,
        "pricetick": body.pricetick,
    }


@router.get("")
def list_guardians() -> dict:
    """全部守护档案（按 watch → healthy → retired 排序）。"""
    return {"guardians": guardian.list_guardians()}


@router.post("/adopt")
def adopt_guardian(
    body: GuardianAdoptBody,
    main_engine: MainEngine = Depends(get_main_engine),
    strategy_engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    """建档：以指定区间的回测结果为基线，状态置 healthy。"""
    try:
        interval = Interval(body.interval)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    strategy_class = strategy_engine.classes.get(body.class_name)
    if not strategy_class:
        raise HTTPException(422, f"策略类不存在: {body.class_name}")
    try:
        record = guardian.adopt(
            main_engine,
            strategy_class,
            body.class_name,
            body.vt_symbol,
            interval,
            body.setting,
            _cost_from_body(body),
            body.baseline_start,
            body.baseline_end,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return record


@router.post("/{guardian_id}/recheck")
def recheck_guardian(
    guardian_id: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """提交单档案复验任务（异步）。"""
    if guardian.get_guardian(guardian_id) is None:
        raise HTTPException(404, f"守护档案不存在: {guardian_id}")

    def _runner(progress) -> dict:
        return {"check": guardian.recheck(main_engine, guardian_id, progress=progress)}

    task = backtest_manager.submit_generic(_runner, kind="guardian", label=f"复验 {guardian_id}")
    return task.to_dict(include_result=False)


@router.post("/recheck-all")
def recheck_all_guardians(
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """提交全量复验任务（异步，跳过已退役）。"""

    def _runner(progress) -> dict:
        return {"results": guardian.recheck_all(main_engine, progress=progress)}

    task = backtest_manager.submit_generic(_runner, kind="guardian", label="全量复验")
    return task.to_dict(include_result=False)


@router.delete("/{guardian_id}")
def drop_guardian(guardian_id: str) -> dict:
    """删除守护档案。"""
    if not guardian.drop(guardian_id):
        raise HTTPException(404, f"守护档案不存在: {guardian_id}")
    return {"dropped": guardian_id}
