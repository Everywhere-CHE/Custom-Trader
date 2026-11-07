"""认证 API 端点：登录、修改密码、当前用户信息、MCP Token 管理。"""

from fastapi import APIRouter, Depends

from ...auth import (
    change_password,
    get_current_user,
    get_mcp_token,
    login,
    refresh_mcp_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def api_login(body: dict) -> dict:
    """登录，返回 JWT token。"""
    username = body.get("username", "")
    password = body.get("password", "")
    return login(username, password)


@router.put("/password")
def api_change_password(
    body: dict,
    username: str = Depends(get_current_user),
) -> dict:
    """修改密码（需登录）。"""
    return change_password(
        username,
        body.get("old_password", ""),
        body.get("new_password", ""),
    )


@router.post("/me")
def api_me(
    username: str = Depends(get_current_user),
) -> dict:
    """验证 Token 是否有效（POST 请求，Token 在 Header 中）。"""
    return {"username": username}


@router.get("/mcp-token")
def api_get_mcp_token(
    username: str = Depends(get_current_user),
) -> dict:
    """获取 MCP 长效 Token（首次访问自动生成），供导出客户端脚本使用。"""
    return {"token": get_mcp_token()}


@router.post("/mcp-token/refresh")
def api_refresh_mcp_token(
    username: str = Depends(get_current_user),
) -> dict:
    """轮换 MCP Token，旧 Token 立即失效。"""
    return {"token": refresh_mcp_token()}