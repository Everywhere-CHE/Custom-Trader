"""通知渠道端点：查看/修改配置、发送测试消息。"""

from fastapi import APIRouter, Depends

from ...config import get_settings
from ...notify_config import load_notify_config, masked, save_notify_config
from ..schemas import NotifyConfigBody
from ...services.notify import test_all

router = APIRouter(prefix="/notify", tags=["notify"])


@router.get("/config")
def get_notify_config() -> dict:
    """当前通知配置（敏感字段脱敏）。"""
    return masked(load_notify_config(get_settings()))


@router.put("/config")
def update_notify_config(body: NotifyConfigBody) -> dict:
    """更新通知配置（敏感字段留空 = 保留原值，传 "-" 清除）。"""
    updates = body.model_dump()
    # 前端回显的是脱敏值（***xx），原样写回会覆盖真值——视为"保留"
    for key in ("feishu_secret", "email_password"):
        value = updates.get(key)
        if isinstance(value, str) and value.startswith("***"):
            updates[key] = ""
    config = save_notify_config(get_settings(), updates)
    return masked(config)


@router.post("/test")
def send_test_notify() -> dict:
    """向所有已配置渠道发送测试消息（同步等待结果）。"""
    return {"results": test_all()}
