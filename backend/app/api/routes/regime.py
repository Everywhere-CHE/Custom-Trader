"""市场环境（Regime）与数据健康端点。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...core.engine.main_engine import MainEngine
from ...services import data_health, market_regime
from ..deps import get_main_engine

router = APIRouter(tags=["regime"])


@router.get("/regime")
def get_regime(
    lookback: int = 30,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """自选池 4 维加权 5 档市场环境 + 近 N 日历史。"""
    try:
        return market_regime.compute_regime(main_engine, lookback_days=min(max(lookback, 5), 60))
    except RuntimeError as e:
        return {"status": "insufficient", "hint": str(e)}


class RepairBody(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    days: int = Field(400, ge=30, le=1500)


@router.get("/data-health")
def get_data_health(
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """日 K 健康检查：OHLC 逻辑 / 缺口 / 停更 / 样本不足。"""
    return data_health.check_all(main_engine)


@router.post("/data-health/repair")
def repair_data(
    body: RepairBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """对问题标的重新下载日 K 修复（FUYAO 优先、网关回退）。"""
    if not body.symbols:
        return {"results": [], "hint": "未指定标的"}
    return {"results": data_health.repair_symbols(main_engine, body.symbols, body.days)}


@router.get("/market-phase")
def get_market_phase(
    force: bool = Query(False),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """市场情绪：全市场连板梯队 + 情绪周期 6 阶段（冰点/启动/主升/高潮/退潮/修复）。"""
    from ...services.market_phase import get_market_phase_cached

    return get_market_phase_cached(force=force)

@router.get("/abnormal")
def get_abnormal_moves(
    force: bool = Query(False),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """异动监控：交易所 3 日偏离口径预警（触发/边缘/关注）+ 10/30 日参考。"""
    from ...services.abnormal_moves import compute_abnormal

    return compute_abnormal(force=force)

@router.get("/screener/strategies")
def list_screener_strategies() -> dict:
    """内置扫描策略目录。"""
    from ...services.screener import strategy_catalog

    return {"strategies": strategy_catalog()}


class ScreenerRunBody(BaseModel):
    strategies: list[str] = Field(default_factory=list, description="策略 id 列表，空=全部")
    force: bool = False


@router.post("/screener/run")
def run_screener_scan(
    body: ScreenerRunBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """全市场扫描（30 分钟缓存）。"""
    from ...services.screener import get_scan_cached

    return get_scan_cached(body.strategies, force=body.force)
