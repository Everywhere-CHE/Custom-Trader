"""认证模块：JWT 登录、密码修改、Token 验证、MCP 长效 Token。

密码和 JWT 密钥存储在 ``data/auth_config.json`` 中。
"""

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# JWT 配置
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
AUTH_CONFIG_PATH = Path.cwd() / "data" / "auth_config.json"

security = HTTPBearer(auto_error=False)


def _load_config() -> dict:
    """加载 auth 配置，不存在时创建默认配置。"""
    if AUTH_CONFIG_PATH.exists():
        return json.loads(AUTH_CONFIG_PATH.read_text(encoding="utf-8"))

    # 首次运行：生成随机 JWT 密钥，创建默认用户 admin/admin
    secret = secrets.token_hex(32)
    pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
    config = {
        "jwt_secret": secret,
        "users": {
            "admin": {
                "password_hash": pw_hash,
                "token_version": 1,
            }
        },
    }
    AUTH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config


def _save_config(config: dict) -> None:
    """保存 auth 配置。"""
    AUTH_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码。"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def hash_password(password: str) -> str:
    """生成密码哈希。"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_token(username: str) -> str:
    """生成 JWT Token（含当前 token_version，改密后旧 token 自动失效）。"""
    config = _load_config()
    user = config.get("users", {}).get(username, {})
    token_version = user.get("token_version", 1)
    payload = {
        "sub": username,
        "ver": token_version,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, config["jwt_secret"], algorithm=ALGORITHM)


def verify_token(token: str) -> str:
    """验证 JWT Token，返回用户名。"""
    config = _load_config()
    try:
        payload = jwt.decode(token, config["jwt_secret"], algorithms=[ALGORITHM])
        username = payload["sub"]
        # 检查 token_version 是否匹配（改密后旧 token 失效）
        # 兼容旧 token（无 ver 字段）视为有效
        user = config.get("users", {}).get(username, {})
        token_ver = payload.get("ver")
        if token_ver is not None and token_ver != user.get("token_version", 1):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="token invalidated (password changed)",
            )
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """依赖注入：从请求头获取当前用户名。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(credentials.credentials)


def get_mcp_token() -> str:
    """获取（首次自动生成）MCP 长效 Token，供 AI 助手客户端远程接入。"""
    config = _load_config()
    token = config.get("mcp_token")
    if not token:
        token = secrets.token_hex(24)
        config["mcp_token"] = token
        _save_config(config)
    return token


def refresh_mcp_token() -> str:
    """轮换 MCP Token，旧 Token 立即失效。"""
    config = _load_config()
    token = secrets.token_hex(24)
    config["mcp_token"] = token
    _save_config(config)
    return token


def verify_mcp_token(token: str) -> bool:
    """校验 MCP 长效 Token。"""
    config = _load_config()
    stored = config.get("mcp_token", "")
    return bool(stored) and secrets.compare_digest(token, stored)


def login(username: str, password: str) -> dict:
    """登录验证，成功返回 token。"""
    config = _load_config()
    user = config.get("users", {}).get(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )
    token = create_token(username)
    return {"access_token": token, "token_type": "bearer", "username": username}


def change_password(username: str, old_password: str, new_password: str) -> dict:
    """修改密码。"""
    config = _load_config()
    user = config.get("users", {}).get(username)
    if not user or not verify_password(old_password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="旧密码错误",
        )
    if len(new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="密码长度至少 4 位",
        )
    user["password_hash"] = hash_password(new_password)
    user["token_version"] = user.get("token_version", 1) + 1
    _save_config(config)
    return {"message": "密码修改成功，所有设备已强制退出"}