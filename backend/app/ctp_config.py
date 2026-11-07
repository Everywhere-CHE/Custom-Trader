"""CTP 连接配置：运行时可编辑，持久化到磁盘。

与 AI 模型配置模式（ data/ai_config.json ）一致：CTP 账户
可以直接从前端 / REST API 修改，无需编辑 .env 或重启。
同时支持 SimNow 测试账户和真实券商柜台 — 任何兼容 CTP
的前置均可（实盘交易需要期货公司提供的前置地址、
BrokerID、AppID 和授权码）。

优先级：data/ctp_config.json（运行时）> .env（初始默认值）。
"""

import json
from pathlib import Path

from .config import DATA_DIR
from typing import Any

CONFIG_PATH = DATA_DIR / "ctp_config.json"

# 常用服务器预设（供前端快速填充）。用户也可以手动输入任意
# 期货公司的前置地址 — 真实期货公司会在开户时提供这些信息
# （通常有多条线路：电信/联通/移动）。
CTP_SERVER_PRESETS: list[dict] = [
    # ----- 第一套：交易时段环境（30xxx 端口）-----
    {
        "id": "simnow-session-g1",
        "label": "SimNow 交易时段（第一组·30xxx）",
        "note": "交易时段 09:00-15:15/21:00-02:30 可用；实时行情，看穿式前置",
        "brokerid": "9999",
        "td_address": "tcp://182.254.243.31:30001",
        "md_address": "tcp://182.254.243.31:30011",
        "appid": "simnow_client_test",
        "auth_code": "0000000000000000",
    },
    {
        "id": "simnow-session-g2",
        "label": "SimNow 交易时段（第二组·30xxx）",
        "note": "交易时段 09:00-15:15/21:00-02:30 可用；第一组连不上时切换",
        "brokerid": "9999",
        "td_address": "tcp://182.254.243.31:30002",
        "md_address": "tcp://182.254.243.31:30012",
        "appid": "simnow_client_test",
        "auth_code": "0000000000000000",
    },
    {
        "id": "simnow-session-g3",
        "label": "SimNow 交易时段（第三组·30xxx）",
        "note": "交易时段 09:00-15:15/21:00-02:30 可用；前两组连不上时切换",
        "brokerid": "9999",
        "td_address": "tcp://182.254.243.31:30003",
        "md_address": "tcp://182.254.243.31:30013",
        "appid": "simnow_client_test",
        "auth_code": "0000000000000000",
    },
    # ----- 第二套：7x24 环境（40xxx 端口）-----
    {
        "id": "simnow-7x24",
        "label": "SimNow 7x24（40xxx·录播回放）",
        "note": "交易日 16:00~次日09:00 / 非交易日 16:00~次日12:00 可用；"
        "行情为录播，需注册满3个交易日才能使用",
        "brokerid": "9999",
        "td_address": "tcp://182.254.243.31:40001",
        "md_address": "tcp://182.254.243.31:40011",
        "appid": "simnow_client_test",
        "auth_code": "0000000000000000",
    },
    {
        "id": "openctp-tts-7x24",
        "label": "openctp TTS 7x24 仿真",
        "note": "openctp 社区仿真柜台（TTS），账号在 openctp 注册；当前网络可能不可达",
        "brokerid": "9999",
        "td_address": "tcp://121.37.80.177:20002",
        "md_address": "tcp://121.37.80.177:20004",
        "appid": "",
        "auth_code": "",
    },
    {
        "id": "real-broker",
        "label": "实盘期货公司（自填）",
        "note": "地址/BrokerID/AppID/授权码由你的期货公司提供（开户资料或官网）；"
        "实盘前请先用仿真环境充分验证",
        "brokerid": "",
        "td_address": "tcp://",
        "md_address": "tcp://",
        "appid": "",
        "auth_code": "",
    },
]

# 构成 CTP 连接配置的字段
FIELDS = (
    "userid",
    "password",
    "brokerid",
    "td_address",
    "md_address",
    "appid",
    "auth_code",
)


def load_ctp_config(settings: Any) -> dict:
    """生效的 CTP 配置：持久化文件优先于 .env 默认值。"""
    config = settings.ctp_setting()
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


def save_ctp_config(settings: Any, updates: dict) -> dict:
    """将更新合并到持久化配置中并返回结果。

    updates 中的空字符串密码会保留现有密码
    （与 AI api_key 字段的约定一致）。配置（包括密码）
    仅持久化到 data/ctp_config.json — load_ctp_config 会
    优先读取该文件而非 .env — 绝不写回 .env，从而保证
    凭据不会进入（可能被提交到版本库的）dotenv 文件。
    """
    config = load_ctp_config(settings)
    for key in FIELDS:
        if key not in updates:
            continue
        value = updates[key]
        if value is None:
            continue
        if key == "password" and value == "":
            continue  # 保留现有密码
        config[key] = str(value)

    # 保存到 data/ctp_config.json
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return config


def masked(config: dict) -> dict:
    """可安全用于 API 响应的配置副本（密码已脱敏）。"""
    result = dict(config)
    result["password"] = "***" if config.get("password") not in ("", "changeme") else ""
    result["has_password"] = config.get("password") not in ("", "changeme")
    return result
