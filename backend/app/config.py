"""基于 pydantic-settings 的应用配置。

配置值从环境变量和 ``.env`` 文件加载
（完整模板见 ``.env.example``）。
"""

from functools import lru_cache
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# backend/ 目录（ app/ 的父目录）
BACKEND_DIR = Path(__file__).resolve().parent.parent
# 项目根目录（ custom-trader/ ）
PROJECT_DIR = BACKEND_DIR.parent
# 运行时数据目录（ SQLite 数据库、日志、运行时 JSON 配置）。
# CT_DATA_DIR（旧名 QT_DATA_DIR 仍兼容）可覆盖该目录 — 测试用它将所有运行时状态
# （自选列表、ctp/stock/ai 配置）与真实部署隔离。
DATA_DIR = Path(
    os.environ.get("CT_DATA_DIR", "")
    or os.environ.get("QT_DATA_DIR", "")
    or (PROJECT_DIR / "data")
)


class Settings(BaseSettings):
    """全局应用配置。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- 服务器 -----
    app_name: str = "custom-trader"
    app_version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:5173"]

    # ----- 数据库 -----
    database_url: str = "sqlite:///../data/custom_trader.db"
    db_timezone: str = "Asia/Shanghai"
    persist_ticks: bool = True

    # ----- 股票 Gateway（腾讯行情源）-----
    enable_stock_gateway: bool = True
    auto_connect_stock: bool = True
    stock_poll_interval: float = 3.0

    # ----- FUYAO（同花顺金融数据 API，日线历史 + 可选快照行情）-----
    enable_fuyao_gateway: bool = True
    auto_connect_fuyao: bool = True
    fuyao_api_key: str = ""            # 在设置页填写，留空 = 网关不连接
    fuyao_poll_interval: float = 5.0
    fuyao_enable_quotes: bool = False  # 快照轮询默认关闭（免费腾讯源已覆盖实时行情）

    # ----- CTP / SimNow -----
    enable_ctp_gateway: bool = True
    auto_connect_ctp: bool = True
    ctp_userid: str = ""
    ctp_password: str = "changeme"
    ctp_brokerid: str = "9999"
    ctp_td_address: str = "tcp://182.254.243.31:30001"
    ctp_md_address: str = "tcp://182.254.243.31:30011"
    ctp_appid: str = "simnow_client_test"
    ctp_auth_code: str = "0000000000000000"

    # ----- AI 助手（第五阶段）-----
    ai_provider: str = "anthropic"   # anthropic | openai（兼容中转）
    anthropic_api_key: str = ""      # 为空 = 禁用 AI 聊天（503）
    ai_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = ""     # 可选的代理/中转地址
    ai_allow_trading: bool = False   # AI 驱动下单的开关
    ai_max_turns: int = 15           # 工具调用循环上限

    def ctp_setting(self) -> dict:
        """根据环境配置构建 CTP 连接配置字典。"""
        return {
            "userid": self.ctp_userid,
            "password": self.ctp_password,
            "brokerid": self.ctp_brokerid,
            "td_address": self.ctp_td_address,
            "md_address": self.ctp_md_address,
            "appid": self.ctp_appid,
            "auth_code": self.ctp_auth_code,
        }

    @property
    def resolved_database_url(self) -> str:
        """将相对的 SQLite 路径解析为相对 backend 目录的绝对路径。"""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            raw_path = url[len(prefix):]
            path = Path(raw_path)
            if not path.is_absolute():
                path = (BACKEND_DIR / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{path.as_posix()}"
        return url


@lru_cache
def get_settings() -> Settings:
    """缓存的配置单例。"""
    return Settings()
