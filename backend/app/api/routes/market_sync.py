"""全市场数据同步端点：状态、启动（全量/增量）、取消。"""

from fastapi import APIRouter, Query

from ...services.market_sync import market_sync_manager

router = APIRouter(prefix="/market-sync", tags=["market-sync"])


@router.get("/status")
def sync_status() -> dict:
    """同步任务状态 + 持久化进度（上次全量/增量时间、标的数）。"""
    return market_sync_manager.status()


@router.post("/run")
def start_sync(
    mode: str = Query("incremental", pattern="^(full|incremental)$"),
    days: int = Query(800, ge=30, le=3000),
) -> dict:
    """启动同步任务（单飞）：full=全量历史，incremental=近 N 天增量。"""
    return market_sync_manager.start(mode, days)


@router.post("/cancel")
def cancel_sync() -> dict:
    """请求取消当前任务（当前标的完成后停止，进度已落库）。"""
    ok = market_sync_manager.request_cancel()
    return {"cancelled": ok}
