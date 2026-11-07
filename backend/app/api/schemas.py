"""Pydantic API 模式定义与领域对象序列化辅助函数。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.constant import Direction, Exchange, Offset, OrderType
from ..core.object import (
    AccountData,
    BarData,
    ContractData,
    LogData,
    OrderData,
    PositionData,
    TickData,
    TradeData,
)

# ----------------------------------------------------------------------
# 请求体
# ----------------------------------------------------------------------


class ConnectRequestBody(BaseModel):
    """Gateway 连接配置（为空 = 使用默认值）。"""

    setting: dict[str, Any] = Field(default_factory=dict)


class SubscribeRequestBody(BaseModel):
    vt_symbols: list[str]


class PlaceOrderBody(BaseModel):
    vt_symbol: str
    direction: Direction
    type: OrderType = OrderType.LIMIT
    volume: float = Field(gt=0)
    price: float = 0
    offset: Offset = Offset.OPEN
    gateway_name: str = ""
    reference: str = ""


class CreateStrategyBody(BaseModel):
    class_name: str
    name: str
    vt_symbol: str = ""
    vt_symbols: list[str] = Field(default_factory=list)
    setting: dict[str, Any] = Field(default_factory=dict)


class EditStrategyBody(BaseModel):
    setting: dict[str, Any]


class WriteStrategyFileBody(BaseModel):
    filename: str
    code: str
    overwrite: bool = False


class ChatMessageBody(BaseModel):
    role: str
    content: Any  # str 或 Anthropic 内容块


class ChatRequestBody(BaseModel):
    messages: list[ChatMessageBody]
    conversation_id: str | None = None
    display_messages: list[dict] | None = None


class AiConfigBody(BaseModel):
    provider: str | None = None    # anthropic | openai
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None     # 为空/None 时保留现有密钥
    allow_trading: bool | None = None
    auto_optimize: bool | None = None


class CtpConfigBody(BaseModel):
    """CTP 账户/服务器配置更新（所有字段均可选）。"""

    userid: str | None = None
    password: str | None = None    # 为空/None 时保留现有密码
    brokerid: str | None = None
    td_address: str | None = None
    md_address: str | None = None
    appid: str | None = None
    auth_code: str | None = None


class StockConfigBody(BaseModel):
    """STOCK gateway 配置更新（所有字段均可选）。"""

    poll_interval: float | None = None


class FuyaoConfigBody(BaseModel):
    """FUYAO（同花顺金融数据 API）配置更新（所有字段均可选）。

    api_key 传空/null 保留原 Key，传 "-" 显式清除。
    """

    api_key: str | None = None
    poll_interval: float | None = None
    enable_quotes: bool | None = None


class BacktestRequestBody(BaseModel):
    class_name: str = ""
    class_names: list[str] = Field(default_factory=list)
    vt_symbol: str = ""
    vt_symbols: list[str] = Field(default_factory=list)
    interval: str = "1m"
    start: datetime
    end: datetime | None = None
    capital: float = 1_000_000
    rate: float = 0.0001
    slippage: float = 0
    size: float = 1
    pricetick: float = 0.01
    setting: dict[str, Any] = Field(default_factory=dict)


class DownloadDataBody(BaseModel):
    vt_symbol: str
    interval: str = "1m"
    start: datetime
    end: datetime | None = None
    gateway_name: str = ""


class GridPreviewBody(BaseModel):
    """网格预览请求（只需网格本身）。"""

    grid: dict[str, Any] = Field(default_factory=dict)


class OptionSyncBody(BaseModel):
    """期权数据同步请求。

    scope：varieties（品种）/ contracts（合约目录）/ details（指定
    标的详情补全）/ all（品种+目录）。
    """

    scope: str = "all"
    underlying: str | None = None


class OptionOptimizeBody(BaseModel):
    """期权模板参数优化请求（robust / walk-forward）。"""

    mode: str = "robust"  # robust | walkforward
    class_name: str
    underlying: str
    start: datetime
    end: datetime | None = None
    metric: str = "sharpe_ratio"
    grid: dict[str, Any] = Field(default_factory=dict)
    setting: dict[str, Any] = Field(default_factory=dict)
    capital: float = 100_000
    commission: float = 0
    slippage: float = 0
    risk_free: float = 0.02
    iv_fallback: float = 0.25
    margin_coef: float | None = None
    margin_limit_pct: float = 0.8
    strike_range_pct: float = 0.25
    k_segments: int = Field(3, ge=2, le=6)
    train_days: int = Field(60, ge=14, le=400)
    test_days: int = Field(20, ge=5, le=200)
    step_days: int = Field(20, ge=5, le=200)


class OptionBacktestBody(BaseModel):
    """期权组合回测请求。"""

    class_name: str
    underlying: str
    start: datetime
    end: datetime | None = None
    capital: float = 1_000_000
    commission: float = 0  # 每张手续费；0=品种默认
    slippage: float = 0  # 每张滑点
    risk_free: float = 0.02
    iv_fallback: float = 0.25
    margin_coef: float | None = None  # None=按合约（默认12%）
    margin_limit_pct: float = 0.8
    strike_range_pct: float = 0.25
    setting: dict[str, Any] = Field(default_factory=dict)


class OptimizeBody(BaseModel):
    """参数优化请求（稳健筛选 / Walk-Forward 共用）。"""

    mode: str = "robust"  # robust | walkforward
    class_name: str
    vt_symbol: str
    interval: str = "d"
    start: datetime
    end: datetime | None = None
    metric: str = "sharpe_ratio"
    grid: dict[str, Any] = Field(default_factory=dict)
    setting: dict[str, Any] = Field(default_factory=dict)
    # 成本参数（与 BacktestRequestBody 同名同默认）
    capital: float = 1_000_000
    rate: float = 0.0001
    slippage: float = 0
    size: float = 1
    pricetick: float = 0.01
    # robust 模式
    k_segments: int = Field(3, ge=2, le=6)
    # walkforward 模式（自然日）
    train_days: int = Field(252, ge=30, le=1500)
    test_days: int = Field(63, ge=14, le=500)
    step_days: int = Field(63, ge=14, le=500)


class GuardianAdoptBody(BaseModel):
    """策略守护建档请求。"""

    class_name: str
    vt_symbol: str
    interval: str = "d"
    setting: dict[str, Any] = Field(default_factory=dict)
    baseline_start: datetime
    baseline_end: datetime
    # 成本参数（复验时保持一致才有可比性）
    capital: float = 1_000_000
    rate: float = 0.0001
    slippage: float = 0
    size: float = 1
    pricetick: float = 0.01


class NotifyConfigBody(BaseModel):
    """通知渠道配置（飞书/企微/邮件/系统通知）。"""

    feishu_webhook: str = ""
    feishu_secret: str = ""
    wecom_webhook: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 465
    email_smtp_ssl: bool = True
    email_username: str = ""
    email_password: str = ""
    email_to: list[str] = Field(default_factory=list)
    system_notify: bool = False
    # 事件开关：哪些事件走推送
    notify_trades: bool = False
    notify_risk: bool = True
    notify_strategy: bool = False
    notify_guardian: bool = True


class ConfigBackupItem(BaseModel):
    """配置备份的类别清单。"""

    strategies: bool = True
    watchlist: bool = True
    gateway_configs: bool = True
    ai_conversations: bool = False
    notify_config: bool = True
    risk_settings: bool = True
    ui_preferences: bool = True


class RiskSettingsBody(BaseModel):
    active: bool | None = None
    order_flow_limit: int | None = None
    order_flow_clear: int | None = None
    order_size_limit: float | None = None
    order_count_limit: int | None = None
    active_order_limit: int | None = None
    trade_count_limit: int | None = None
    position_limit: float | None = None
    daily_loss_limit_pct: float | None = None
    enable_order_size: bool | None = None
    enable_order_flow: bool | None = None
    enable_order_count: bool | None = None
    enable_active_order: bool | None = None
    enable_trade_count: bool | None = None
    enable_position_limit: bool | None = None
    enable_daily_loss_breaker: bool | None = None
    enable_trailing_stop: bool | None = None
    trailing_stops: dict[str, float] | None = None
    enable_order_flow: bool | None = None
    enable_order_count: bool | None = None
    enable_active_order: bool | None = None
    enable_trade_count: bool | None = None
    enable_position_limit: bool | None = None
    enable_daily_loss_breaker: bool | None = None


# ----------------------------------------------------------------------
# 领域数据类序列化 -> JSON 安全字典
# （REST 响应和 WebSocket 推送共同使用）
# ----------------------------------------------------------------------


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_tick(tick: TickData) -> dict:
    data = {
        "vt_symbol": tick.vt_symbol,
        "symbol": tick.symbol,
        "exchange": tick.exchange.value,
        "datetime": _dt(tick.datetime),
        "name": tick.name,
        "volume": tick.volume,
        "turnover": tick.turnover,
        "open_interest": tick.open_interest,
        "last_price": tick.last_price,
        "limit_up": tick.limit_up,
        "limit_down": tick.limit_down,
        "open_price": tick.open_price,
        "high_price": tick.high_price,
        "low_price": tick.low_price,
        "pre_close": tick.pre_close,
        "gateway_name": tick.gateway_name,
    }
    for level in range(1, 6):
        for prefix in ("bid_price", "ask_price", "bid_volume", "ask_volume"):
            key = f"{prefix}_{level}"
            data[key] = getattr(tick, key)
    return data


def serialize_order(order: OrderData) -> dict:
    return {
        "vt_orderid": order.vt_orderid,
        "vt_symbol": order.vt_symbol,
        "symbol": order.symbol,
        "exchange": order.exchange.value,
        "orderid": order.orderid,
        "type": order.type.value,
        "direction": order.direction.value if order.direction else None,
        "offset": order.offset.value,
        "price": order.price,
        "volume": order.volume,
        "traded": order.traded,
        "status": order.status.value,
        "datetime": _dt(order.datetime),
        "reference": order.reference,
        "gateway_name": order.gateway_name,
    }


def serialize_trade(trade: TradeData) -> dict:
    return {
        "vt_tradeid": trade.vt_tradeid,
        "vt_orderid": trade.vt_orderid,
        "vt_symbol": trade.vt_symbol,
        "symbol": trade.symbol,
        "exchange": trade.exchange.value,
        "orderid": trade.orderid,
        "tradeid": trade.tradeid,
        "direction": trade.direction.value if trade.direction else None,
        "offset": trade.offset.value,
        "price": trade.price,
        "volume": trade.volume,
        "datetime": _dt(trade.datetime),
        "gateway_name": trade.gateway_name,
        "pnl": 0.0,  # 在 list_trades 端点中计算
    }


def serialize_position(position: PositionData) -> dict:
    return {
        "vt_positionid": position.vt_positionid,
        "vt_symbol": position.vt_symbol,
        "symbol": position.symbol,
        "exchange": position.exchange.value,
        "direction": position.direction.value,
        "volume": position.volume,
        "frozen": position.frozen,
        "price": position.price,
        "pnl": position.pnl,
        "yd_volume": position.yd_volume,
        "gateway_name": position.gateway_name,
        "datetime": _dt(position.datetime),
    }


def serialize_account(account: AccountData) -> dict:
    return {
        "vt_accountid": account.vt_accountid,
        "accountid": account.accountid,
        "balance": account.balance,
        "frozen": account.frozen,
        "available": account.available,
        "gateway_name": account.gateway_name,
    }


def serialize_contract(contract: ContractData) -> dict:
    return {
        "vt_symbol": contract.vt_symbol,
        "symbol": contract.symbol,
        "exchange": contract.exchange.value,
        "name": contract.name,
        "product": contract.product.value,
        "size": contract.size,
        "pricetick": contract.pricetick,
        "min_volume": contract.min_volume,
        "stop_supported": contract.stop_supported,
        "net_position": contract.net_position,
        "history_data": contract.history_data,
        "gateway_name": contract.gateway_name,
    }


def serialize_log(log: LogData) -> dict:
    return {
        "time": _dt(log.time),
        "level": log.level,
        "msg": log.msg,
        "gateway_name": log.gateway_name,
    }


def serialize_bar(bar: BarData) -> dict:
    return {
        "vt_symbol": bar.vt_symbol,
        "symbol": bar.symbol,
        "exchange": bar.exchange.value,
        "datetime": _dt(bar.datetime),
        "interval": bar.interval.value if bar.interval else None,
        "volume": bar.volume,
        "turnover": bar.turnover,
        "open_interest": bar.open_interest,
        "open_price": bar.open_price,
        "high_price": bar.high_price,
        "low_price": bar.low_price,
        "close_price": bar.close_price,
    }
