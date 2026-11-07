"""聚合 API 路由。"""

from fastapi import APIRouter
from fastapi.responses import Response

from . import (
    ai,
    auth,
    backtest,
    backup,
    conversations,
    gateways,
    guardian,
    health,
    logs,
    market,
    market_sync,
    mcp,
    monitor,
    notify,
    options,
    paper,
    portfolio,
    regime,
    risk,
    strategies,
    trading,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(gateways.router)
api_router.include_router(market.router)
api_router.include_router(market_sync.router)
api_router.include_router(guardian.router)
api_router.include_router(notify.router)
api_router.include_router(backup.router)
api_router.include_router(monitor.router)
api_router.include_router(paper.router)
api_router.include_router(regime.router)
api_router.include_router(trading.router)
api_router.include_router(portfolio.router)
api_router.include_router(logs.router)
api_router.include_router(strategies.router)
api_router.include_router(backtest.router)
api_router.include_router(options.router)
api_router.include_router(risk.router)
api_router.include_router(ai.router)
api_router.include_router(mcp.router)
api_router.include_router(conversations.router)
api_router.include_router(auth.router)


# 兜底 HEAD 处理器：部分浏览器/工具会向 API 端点发送 HEAD 请求以检查可用性。
# Starlette 不会为仅 GET 的路由自动响应 HEAD 请求，
# 因此我们对任意 /api/* 的 HEAD 请求返回 200。
@api_router.head("/{path:path}", include_in_schema=False)
def head_catch_all(path: str) -> Response:
    return Response(status_code=200)
