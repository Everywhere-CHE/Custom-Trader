"""日志查询端点。"""

from fastapi import APIRouter, Depends

from ...core.engine.main_engine import MainEngine
from ..deps import get_main_engine
from ..schemas import serialize_log

router = APIRouter(tags=["system"])


@router.get("/logs")
def list_logs(
    limit: int = 100,
    level: int | None = None,
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    logs = main_engine.log.get_recent_logs(limit=limit, level=level)
    return [serialize_log(log) for log in logs]
