"""FastAPI 应用入口。

运行方式：
    uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import api_router
from .api.websocket import ws_router
from .auth import verify_mcp_token, verify_token
from .config import get_settings
from .state import AppState


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    state = AppState(settings)
    app.state.qt = state
    state.start(asyncio.get_running_loop())
    # 保持旧属性以兼容 deps.get_main_engine 与 AI 路由的 app.state.* 读取
    app.state.main_engine = state.main_engine
    app.state.ws_manager = state.ws_manager
    app.state.event_bridge = state.event_bridge
    app.state.ai_engine = state.ai_engine
    yield
    await state.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 全局 API 认证中间件：保护所有 /api/* 业务端点
    # 放行：登录、健康检查、静态资源、WebSocket、本地服务（AI 助手等）
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        # 放行无需认证的路径
        if (
            path.startswith("/api/auth/login")
            or path == "/api/health"
            or path.startswith("/ws")
            or path.startswith("/assets")
            or path == "/"
        ):
            return await call_next(request)
        # 本机服务（AI 助手等）免认证：仅限直连（无 X-Forwarded-For 头）
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            # 有 X-Forwarded-For 说明经过了 nginx 代理，不是本机服务
            forwarded = request.headers.get("X-Forwarded-For", "")
            if not forwarded:
                return await call_next(request)
        # 仅 /api/* 业务端点需要认证；其余路径（favicon 等静态资源）放行
        if not path.startswith("/api/"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "not authenticated"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            verify_token(auth[7:])
        except Exception:
            # JWT 失败后 fallback 校验 MCP 长效 Token（AI 助手客户端远程接入）
            if not verify_mcp_token(auth[7:]):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "invalid or expired token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    app.include_router(api_router)
    app.include_router(ws_router)

    # 若存在已构建的前端（ frontend/dist ）则直接托管，这样生产
    # 部署只需在一个端口上运行后端进程。
    # 兼容 PyInstaller 打包（sys._MEIPASS）和普通开发环境
    try:
        meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    except AttributeError:
        meipass = None
    dist_dir = (
        meipass / "frontend" / "dist"
        if meipass
        else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    if dist_dir.exists():
        app.mount(
            "/", StaticFiles(directory=str(dist_dir), html=True), name="frontend"
        )

    return app


app = create_app()
