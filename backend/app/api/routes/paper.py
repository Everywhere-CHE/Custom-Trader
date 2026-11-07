"""模拟盘端点：总览、下单、成交/台账、设置、重置。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.engine.main_engine import MainEngine
from ...services import paper
from ..deps import get_main_engine

router = APIRouter(prefix="/paper", tags=["paper"])


class PaperOrderBody(BaseModel):
    vt_symbol: str
    direction: str = Field(..., description="buy / sell")
    volume: int = Field(..., gt=0)
    reason: str = ""


class PaperSettingsBody(BaseModel):
    capital: float | None = Field(None, gt=0)
    rate: float | None = Field(None, ge=0, le=0.01)
    slippage: float | None = Field(None, ge=0)


@router.get("/overview")
def paper_overview(main_engine: MainEngine = Depends(get_main_engine)) -> dict:
    """账户总览：权益/持仓/盈亏（台账实时重放）。"""
    return paper.replay(main_engine)


@router.post("/order")
def paper_order(
    body: PaperOrderBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """市价单：按最新快照价即时成交。"""
    try:
        return paper.market_order(
            main_engine, body.vt_symbol, body.direction, body.volume, body.reason
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/fills")
def paper_fills(
    limit: int = Query(100, ge=1, le=2000),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """成交台账（最新在前）。"""
    fills = list(reversed(paper.load_fills()))[:limit]
    return {"fills": fills}


@router.put("/settings")
def update_paper_settings(body: PaperSettingsBody) -> dict:
    """更新模拟盘参数（初始资金/费率/滑点）。"""
    return paper.save_settings(body.model_dump(exclude_none=True))


@router.post("/reset")
def reset_paper() -> dict:
    """重置账户（旧台账归档为 fills.<时间戳>.jsonl，不删除）。"""
    return paper.reset()
