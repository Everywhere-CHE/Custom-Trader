"""FUYAO（同花顺金融数据 API）网关连接配置。

与 ``stock_config.py`` / ``ctp_config.py`` 采用相同模式：
运行时可编辑并持久化到磁盘，API Key 在设置页面填写，
无需编辑 .env 或重启。

优先级：data/fuyao_config.json（运行时）> .env（初始默认值）。
"""

import json
from pathlib import Path

from .config import DATA_DIR
from typing import Any

CONFIG_PATH = DATA_DIR / "fuyao_config.json"

FIELDS = ("api_key", "poll_interval", "enable_quotes")


def load_fuyao_config(settings: Any) -> dict:
    """生效的 FUYAO 配置：持久化文件优先于 .env 默认值。"""
    config: dict = {
        "api_key": settings.fuyao_api_key,
        "poll_interval": settings.fuyao_poll_interval,
        "enable_quotes": settings.fuyao_enable_quotes,
    }
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key in FIELDS:
                value = data.get(key)
                if value is not None:
                    config[key] = value
    except Exception:
        pass  # 文件损坏：回退到 .env 中的值
    return config


def save_fuyao_config(settings: Any, updates: dict) -> dict:
    """将更新合并到持久化配置中并返回结果。

    api_key 传 None 或空字符串时保留原有 Key（避免误清空）；
    显式传 "-" 可清除 Key。
    """
    config = load_fuyao_config(settings)
    for key in FIELDS:
        if key not in updates:
            continue
        value = updates[key]
        if value is None:
            continue
        if key == "api_key":
            if value == "-":
                config[key] = ""
            elif str(value).strip():
                config[key] = str(value).strip()
            # 空字符串：保留原 Key
            continue
        config[key] = value

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config


def masked(config: dict) -> dict:
    """返回给前端前对 api_key 脱敏：只暴露尾 4 位。"""
    result = dict(config)
    key = result.get("api_key") or ""
    if len(key) > 8:
        result["api_key"] = f"***{key[-4:]}"
    elif key:
        result["api_key"] = "***"
    return result
