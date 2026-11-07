"""FastAPI 依赖项。"""

from fastapi import Request

from ..core.engine.main_engine import MainEngine


def get_main_engine(request: Request) -> MainEngine:
    return request.app.state.main_engine
