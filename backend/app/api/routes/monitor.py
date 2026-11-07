"""监控中心端点：规则 CRUD、告警查询、手动评估。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.engine.main_engine import MainEngine
from ...services import monitor
from ..deps import get_main_engine

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/rules")
def list_rules() -> dict:
    """全部监控规则。"""
    return {"rules": monitor.load_rules()}


@router.post("/rules", status_code=201)
def create_rule(body: dict) -> dict:
    """新建规则（服务端校验字段白名单）。"""
    try:
        return monitor.add_rule(body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.put("/rules/{rule_id}")
def replace_rule(rule_id: str, body: dict) -> dict:
    """更新规则。"""
    try:
        return monitor.update_rule(rule_id, body)
    except ValueError as e:
        raise HTTPException(422 if "不存在" not in str(e) else 404, str(e)) from None


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: str) -> dict:
    if not monitor.delete_rule(rule_id):
        raise HTTPException(404, f"规则不存在: {rule_id}")
    return {"deleted": rule_id}


@router.patch("/rules/{rule_id}/enabled")
def toggle_rule(rule_id: str, body: dict) -> dict:
    enabled = bool(body.get("enabled", True))
    rule = monitor.set_rule_enabled(rule_id, enabled)
    if rule is None:
        raise HTTPException(404, f"规则不存在: {rule_id}")
    return rule


@router.get("/alerts")
def list_alerts(
    limit: int = Query(100, ge=1, le=1000),
    rule_id: str = "",
) -> dict:
    """告警记录（最新在前）。"""
    return {"alerts": monitor.load_alerts(limit, rule_id)}


@router.delete("/alerts")
def clear_all_alerts() -> dict:
    monitor.clear_alerts()
    return {"cleared": True}


@router.post("/evaluate")
def evaluate_now(main_engine: MainEngine = Depends(get_main_engine)) -> dict:
    """手动评估一轮（调试用），返回触发次数。"""
    engine = getattr(main_engine, "_monitor_engine", None)
    if engine is None:
        raise HTTPException(503, "MonitorEngine not available")
    return {"triggered": engine.evaluate_once()}
