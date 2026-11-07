"""通知渠道配置。

与 ``fuyao_config.py`` 相同模式：运行时可编辑并持久化到
``data/notify_config.json``，设置页修改后立即生效，无需重启。
"""

import json
from pathlib import Path
from typing import Any

from .config import DATA_DIR

CONFIG_PATH = DATA_DIR / "notify_config.json"

FIELDS = (
    "feishu_webhook",
    "feishu_secret",
    "wecom_webhook",
    "email_smtp_host",
    "email_smtp_port",
    "email_smtp_ssl",
    "email_username",
    "email_password",
    "email_to",
    "system_notify",
    "notify_trades",
    "notify_risk",
    "notify_strategy",
    "notify_guardian",
)

# 敏感字段（返回前端时脱敏）
SECRET_FIELDS = ("feishu_secret", "email_password")


def default_config(settings: Any) -> dict:
    return {
        "feishu_webhook": "",
        "feishu_secret": "",
        "wecom_webhook": "",
        "email_smtp_host": "",
        "email_smtp_port": 465,
        "email_smtp_ssl": True,
        "email_username": "",
        "email_password": "",
        "email_to": [],
        "system_notify": False,
        "notify_trades": False,
        "notify_risk": True,
        "notify_strategy": False,
        "notify_guardian": True,
    }


def load_notify_config(settings: Any) -> dict:
    """生效的通知配置：持久化文件优先于默认值。"""
    config = default_config(settings)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key in FIELDS:
                if key in data and data[key] is not None:
                    config[key] = data[key]
    except Exception:
        pass  # 文件损坏：回退默认值
    return config


def save_notify_config(settings: Any, updates: dict) -> dict:
    """将更新合并到持久化配置并返回结果。

    敏感字段传 None 或空字符串时保留原值（避免误清空）；
    显式传 "-" 可清除。
    """
    config = load_notify_config(settings)
    for key in FIELDS:
        if key not in updates:
            continue
        value = updates[key]
        if key in SECRET_FIELDS:
            if value == "-":
                config[key] = ""
            elif value:
                config[key] = str(value).strip()
            # None / 空串：保留原值
            continue
        if value is None:
            continue
        config[key] = value

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config


def masked(config: dict) -> dict:
    """返回给前端前对敏感字段脱敏：只暴露尾 2 位。"""
    result = dict(config)
    for key in SECRET_FIELDS:
        value = result.get(key) or ""
        if len(value) > 4:
            result[key] = f"***{value[-2:]}"
        elif value:
            result[key] = "***"
        else:
            result[key] = ""
    return result
