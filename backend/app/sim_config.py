"""SIM Gateway 连接配置：运行时可编辑，持久化到磁盘。

与 ``ctp_config.py`` 采用相同模式。SIM Gateway 的
tick_interval 和 seed 可以直接从前端 / REST API 修改，
无需编辑 .env 或重启。

优先级：data/sim_config.json（运行时）> .env（初始默认值）。
"""

import json
from pathlib import Path

from .config import DATA_DIR
from typing import Any

CONFIG_PATH = DATA_DIR / "sim_config.json"

FIELDS = ("tick_interval", "seed")


def load_sim_config(settings: Any) -> dict:
    """生效的 SIM 配置：持久化文件优先于 .env 默认值。"""
    config: dict = {
        "tick_interval": settings.sim_tick_interval,
        "seed": 0,
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


def save_sim_config(settings: Any, updates: dict) -> dict:
    """将更新合并到持久化配置中并返回结果。"""
    config = load_sim_config(settings)
    for key in FIELDS:
        if key not in updates:
            continue
        value = updates[key]
        if value is None:
            continue
        config[key] = value

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config