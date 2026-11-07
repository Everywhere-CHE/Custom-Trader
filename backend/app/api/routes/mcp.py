"""MCP HTTP 桥接端点。

供单文件零依赖客户端脚本（``mcp_server/standalone_client.py`` 渲染产物）远程调用：

- ``GET  /api/mcp/tools``  返回 MCP tools/list 格式的工具清单（源自 ``app.ai.tools.TOOL_SPECS``）
- ``POST /api/mcp/call``   执行一次工具调用，复用 AI 助手的 TradingTools（下单权限与其开关一致）
- ``GET  /api/mcp/script`` 下载预置了服务器地址与 MCP Token 的专属客户端脚本（需 JWT 登录）

认证由全局中间件完成：远程请求需 Bearer JWT 或 MCP Token，本机直连免认证。
"""

import re
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...ai.tools import TOOL_SPECS, TradingTools, execute_tool
from ...auth import get_current_user, get_mcp_token
from ...config import get_settings

router = APIRouter(prefix="/mcp", tags=["mcp"])

# base 查询参数白名单：scheme://host[:port]，防止注入生成脚本
_BASE_PATTERN = re.compile(r"^https?://[A-Za-z0-9.\-\[\]]+:?\d{0,5}$")

# 打包后数据文件位于 sys._MEIPASS，源码运行时位于 backend/mcp_server/
_SCRIPT_CANDIDATES = (
    Path(getattr(sys, "_MEIPASS", "")) / "mcp_server" / "standalone_client.py",
    Path(__file__).resolve().parents[3] / "mcp_server" / "standalone_client.py",
    Path.cwd() / "mcp_server" / "standalone_client.py",
)


class McpCallBody(BaseModel):
    name: str = Field(..., description="工具名，见 /api/mcp/tools")
    arguments: dict = Field(default_factory=dict, description="工具参数")


def _tool_definitions() -> list[dict]:
    """把 TOOL_SPECS 转成 MCP tools/list 的工具定义。"""
    tools = []
    for spec in TOOL_SPECS:
        tools.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "inputSchema": spec["input_schema"],
                "annotations": {"readOnlyHint": bool(spec.get("readonly"))},
            }
        )
    return tools


def _get_trading_tools(request: Request) -> TradingTools:
    """复用 AI 助手的 TradingTools 实例，与其下单权限开关保持一致。"""
    engine = getattr(request.app.state, "ai_engine", None)
    if engine is not None and getattr(engine, "tools", None) is not None:
        return engine.tools
    settings = get_settings()
    return TradingTools(
        base_url=f"http://127.0.0.1:{settings.port}/api",
        allow_trading=getattr(settings, "ai_allow_trading", False),
    )


@router.get("/tools")
def api_mcp_tools() -> dict:
    """MCP tools/list 数据源。"""
    return {"tools": _tool_definitions()}


@router.post("/call")
def api_mcp_call(body: McpCallBody, request: Request) -> dict:
    """执行一次 MCP 工具调用，返回文本结果。"""
    spec_names = {s["name"] for s in TOOL_SPECS}
    if body.name not in spec_names:
        raise HTTPException(status_code=404, detail=f"未知工具 {body.name!r}")
    tools = _get_trading_tools(request)
    text = execute_tool(tools, body.name, body.arguments)
    return {"text": text, "is_error": text.startswith("错误")}


@router.get("/script")
def api_mcp_script(
    request: Request,
    username: str = Depends(get_current_user),
) -> Response:
    """下载预置了服务器地址与 MCP Token 的专属客户端脚本（需 JWT 登录）。

    ``base`` 查询参数可指定预置的服务器地址（如 https://host:38443 直连端口、
    http://127.0.0.1:8000 本机），缺省取当前请求的访问地址。
    """
    template = next(
        (p for p in _SCRIPT_CANDIDATES if p and p.is_file()), None
    )
    if template is None:
        raise HTTPException(status_code=500, detail="客户端脚本模板缺失")

    base_param = request.query_params.get("base", "").strip().rstrip("/")
    if base_param:
        if not _BASE_PATTERN.match(base_param):
            raise HTTPException(status_code=422, detail="非法的服务器地址格式")
        base_url = base_param
    else:
        # 脚本内部按 "{host}/api{endpoint}" 拼接，这里只注入源地址（不带 /api）
        base_url = str(request.base_url).rstrip("/")
    token = get_mcp_token()
    code = (
        template.read_text(encoding="utf-8")
        .replace('DEFAULT_HOST = ""', f'DEFAULT_HOST = "{base_url}"')
        .replace('DEFAULT_TOKEN = ""', f'DEFAULT_TOKEN = "{token}"')
    )
    return Response(
        content=code,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="custom_trader_mcp.py"'},
    )
