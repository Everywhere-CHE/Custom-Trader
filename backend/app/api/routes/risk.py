"""风险管理端点。"""

from fastapi import APIRouter, Depends, HTTPException

from ...core.engine.main_engine import MainEngine
from ...core.engine.risk_engine import RiskEngine
from ..deps import get_main_engine
from ..schemas import RiskSettingsBody

router = APIRouter(prefix="/risk", tags=["risk"])


def get_risk_engine(
    main_engine: MainEngine = Depends(get_main_engine),
) -> RiskEngine:
    engine = main_engine.get_engine("risk")
    if not isinstance(engine, RiskEngine):
        raise HTTPException(503, "RiskEngine not available")
    return engine


@router.get("")
def get_risk(engine: RiskEngine = Depends(get_risk_engine)) -> dict:
    return {"settings": engine.get_settings(), "status": engine.get_status()}


@router.put("")
def update_risk(
    body: RiskSettingsBody,
    engine: RiskEngine = Depends(get_risk_engine),
) -> dict:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    settings = engine.update_settings(updates)
    return {"settings": settings, "status": engine.get_status()}


@router.post("/reset")
def reset_risk(engine: RiskEngine = Depends(get_risk_engine)) -> dict:
    engine.reset()
    return {"settings": engine.get_settings(), "status": engine.get_status()}


@router.put("/trailing-stop")
def update_trailing_stop(
    body: dict,
    engine: RiskEngine = Depends(get_risk_engine),
) -> dict:
    """更新单个合约的移动止损配置。"""
    key = body.get("key", "")
    pct = body.get("pct")
    if not key:
        raise HTTPException(422, "key is required")
    if pct is not None:
        if not isinstance(pct, (int, float)) or pct <= 0:
            raise HTTPException(422, "pct must be a positive number")
        engine.trailing_stops[key] = float(pct)
    else:
        engine.trailing_stops.pop(key, None)
    engine._save_settings()
    return {"trailing_stops": dict(engine.trailing_stops)}


@router.delete("/trailing-stop/{key}")
def delete_trailing_stop(
    key: str,
    engine: RiskEngine = Depends(get_risk_engine),
) -> dict:
    """删除指定合约的移动止损配置。"""
    engine.trailing_stops.pop(key, None)
    engine._save_settings()
    return {"trailing_stops": dict(engine.trailing_stops)}
