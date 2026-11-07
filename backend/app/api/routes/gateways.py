"""网关管理端点。"""

import time as _time

import requests as _requests

from fastapi import APIRouter, Depends, HTTPException

from ...config import get_settings
from ...core.engine.main_engine import MainEngine
from ...core.gateway.fuyao.fuyao_gateway import (
    BASE_URL as FUYAO_BASE_URL,
    CALENDAR_URL as FUYAO_CALENDAR_URL,
    FuyaoApiError,
    FuyaoGateway,
)
from ...core.object import SubscribeRequest
from ...core.utility import extract_vt_symbol
from ...ctp_config import (
    CTP_SERVER_PRESETS,
    load_ctp_config,
    masked,
    save_ctp_config,
)
from ...fuyao_config import (
    load_fuyao_config,
    masked as mask_fuyao_config,
    save_fuyao_config,
)
from ...stock_config import load_stock_config, save_stock_config
from ..deps import get_main_engine
from ..schemas import (
    ConnectRequestBody,
    CtpConfigBody,
    FuyaoConfigBody,
    StockConfigBody,
    SubscribeRequestBody,
)

router = APIRouter(prefix="/gateways", tags=["gateways"])

SENSITIVE_KEYWORDS = ("password", "secret", "token", "auth", "key")


def _mask_setting(setting: dict) -> dict:
    """返回前对配置结构中的敏感字段进行脱敏。"""
    result = {}
    for key, value in setting.items():
        if any(word in key.lower() for word in SENSITIVE_KEYWORDS):
            result[key] = "***"
        else:
            result[key] = value
    return result


@router.get("")
def list_gateways(
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    result = []
    for name, gateway in main_engine.gateways.items():
        result.append(
            {
                "name": name,
                "connected": gateway.connected,
                "default_setting": _mask_setting(gateway.get_default_setting()),
                "exchanges": [ex.value for ex in gateway.exchanges],
            }
        )
    return result


# ----------------------------------------------------------------------
# CTP 连接配置（运行时可编辑并持久化；支持 SimNow 或真实
# 期货公司柜台）。注意：必须声明在 /{gateway_name} 路径之前。
# ----------------------------------------------------------------------


@router.get("/ctp/config")
def get_ctp_config() -> dict:
    """当前生效的 CTP 配置（密码已脱敏）。"""
    return masked(load_ctp_config(get_settings()))


@router.get("/ctp/presets")
def get_ctp_presets() -> list[dict]:
    """常见 CTP 服务器预设（SimNow / openctp TTS / 实盘）。"""
    return CTP_SERVER_PRESETS


@router.put("/ctp/config")
def update_ctp_config(body: CtpConfigBody) -> dict:
    """更新已持久化的 CTP 账户/服务器配置。

    密码为空时保留原有密码。配置在下次连接时生效；
    如当前已连接，请在设置页面断开并重新连接以切换账户。
    """
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    config = save_ctp_config(get_settings(), updates)
    return masked(config)


# ----------------------------------------------------------------------
# STOCK 网关配置（运行时可编辑）
# ----------------------------------------------------------------------


@router.get("/stock/config")
def get_stock_config() -> dict:
    """当前生效的 STOCK 网关配置。"""
    cfg = load_stock_config(get_settings())
    return {
        "poll_interval": cfg.get("poll_interval", 3.0),
    }


@router.put("/stock/config")
def update_stock_config(body: StockConfigBody) -> dict:
    """更新已持久化的 STOCK 网关配置。"""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    config = save_stock_config(get_settings(), updates)
    return config


# ----------------------------------------------------------------------
# FUYAO 网关配置（同花顺金融数据 API；运行时可编辑）
# ----------------------------------------------------------------------


@router.get("/fuyao/config")
def get_fuyao_config() -> dict:
    """当前生效的 FUYAO 配置（API Key 已脱敏，只暴露尾 4 位）。"""
    cfg = load_fuyao_config(get_settings())
    return mask_fuyao_config(cfg)


@router.put("/fuyao/config")
def update_fuyao_config(body: FuyaoConfigBody) -> dict:
    """更新已持久化的 FUYAO 配置。

    api_key 传空/null 保留原 Key，传 "-" 清除。配置保存后需断开
    FUYAO 再重新连接以生效。
    """
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    config = save_fuyao_config(get_settings(), updates)
    return mask_fuyao_config(config)


@router.post("/fuyao/test")
def test_fuyao() -> dict:
    """FUYAO 数据源自动调试：依次执行连通性 / Key 校验 / 历史K线探测。

    无需先连接网关；未配置 Key 时也能运行（Key 校验项会失败并给出
    原因），用于在填写 Key 前后快速判断数据源可用性。
    """
    cfg = load_fuyao_config(get_settings())
    api_key = str(cfg.get("api_key") or "").strip()

    checks: list[dict] = []

    def _run(name: str, fn) -> None:
        started = _time.perf_counter()
        try:
            detail = fn()
            checks.append(
                {
                    "name": name,
                    "ok": True,
                    "detail": detail,
                    "latency_ms": int((_time.perf_counter() - started) * 1000),
                }
            )
        except Exception as e:  # noqa: BLE001 - 自检需要吞掉一切异常
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "detail": str(e),
                    "latency_ms": int((_time.perf_counter() - started) * 1000),
                }
            )

    session = _requests.Session()
    session.headers.update({"X-api-key": api_key})

    def _check_reachability() -> str:
        # 不带 Key 直接请求日历接口：无论返回 2001 还是 0，
        # 只要拿到结构化信封就说明服务可达、响应格式正确
        resp = session.get(FUYAO_CALENDAR_URL, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        code = payload.get("code")
        if code in (0, 2001, 2003):
            return f"服务可达（{FUYAO_BASE_URL}），响应信封格式正确"
        raise RuntimeError(f"异常响应 code={code}")

    def _check_key() -> str:
        if not api_key:
            raise RuntimeError(
                "未配置 API Key（设置 → 网关管理 → FUYAO 数据源设置；"
                "Key 在 fuyao.aicubes.cn /admin/ 页签发）"
            )
        data = session.get(FUYAO_CALENDAR_URL, timeout=10).json()
        if data.get("code") == 2001:
            raise RuntimeError("API Key 无效（code=2001）")
        if data.get("code") == 2003:
            raise RuntimeError("API Key 无权访问行情能力（code=2003）")
        if data.get("code") != 0:
            raise RuntimeError(f"code={data.get('code')} {data.get('message')}")
        days = len((data.get("data") or {}).get("item") or [])
        return f"Key 有效，交易日历返回 {days} 个交易日"

    def _check_historical() -> str:
        from datetime import datetime, timedelta

        end = datetime.now()
        start = end - timedelta(days=30)
        resp = session.get(
            f"{FUYAO_BASE_URL}/api/a-share/prices/historical",
            params={
                "thscode": "600519.SH",
                "interval": "1d",
                "start": int(start.timestamp() * 1000),
                "end": int(end.timestamp() * 1000),
                "adjust": "forward",
            },
            timeout=15,
        )
        payload = resp.json()
        if payload.get("code") != 0:
            raise FuyaoApiError(
                int(payload.get("code", -1)), str(payload.get("message", ""))
            )
        bars = (payload.get("data") or {}).get("item") or []
        if not bars:
            raise RuntimeError("返回 0 根K线")
        first = bars[0]
        return (
            f"600519.SH 近30日返回 {len(bars)} 根日线，"
            f"最新收盘 {first.get('close_price')}"
        )

    _run("服务连通性", _check_reachability)
    _run("API Key 校验", _check_key)
    _run("历史K线探测（600519.SH）", _check_historical)

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "api_key_configured": bool(api_key), "checks": checks}


@router.post("/{gateway_name}/connect", status_code=202)
def connect_gateway(
    gateway_name: str,
    body: ConnectRequestBody | None = None,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    gateway = main_engine.gateways.get(gateway_name)
    if not gateway:
        raise HTTPException(404, f"Gateway not found: {gateway_name}")

    setting = body.setting if body else {}
    # 对于 CTP / STOCK / FUYAO，请求体为空时使用已持久化的运行时配置
    # （其本身会回退到 .env），因此密码 / API Key 永远不会经由
    # HTTP 请求传输。
    if gateway_name == "CTP" and not setting:
        setting = load_ctp_config(get_settings())
    if gateway_name == "STOCK" and not setting:
        setting = load_stock_config(get_settings())
    if gateway_name == "FUYAO" and not setting:
        setting = load_fuyao_config(get_settings())
    main_engine.connect(setting, gateway_name)

    # 重新执行自选列表的自动订阅，让待订阅的合约（如等待 CTP
    # 合约信息的期货）在本次连接后开始推送行情。
    from ...state import _start_watchlist_autosubscribe

    _start_watchlist_autosubscribe(main_engine)
    return {"accepted": True, "gateway_name": gateway_name}


@router.post("/{gateway_name}/disconnect", status_code=202)
def disconnect_gateway(
    gateway_name: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """关闭网关连接（例如在切换 CTP 账户之前）。"""
    gateway = main_engine.gateways.get(gateway_name)
    if not gateway:
        raise HTTPException(404, f"Gateway not found: {gateway_name}")
    if not gateway.connected:
        return {"accepted": True, "gateway_name": gateway_name,
                "note": "already disconnected"}
    gateway.close()
    return {"accepted": True, "gateway_name": gateway_name}


@router.post("/{gateway_name}/subscribe", status_code=202)
def subscribe(
    gateway_name: str,
    body: SubscribeRequestBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    gateway = main_engine.gateways.get(gateway_name)
    if not gateway:
        raise HTTPException(404, f"Gateway not found: {gateway_name}")
    if not gateway.connected:
        raise HTTPException(409, f"Gateway not connected: {gateway_name}")

    subscribed = []
    for vt_symbol in body.vt_symbols:
        try:
            symbol, exchange = extract_vt_symbol(vt_symbol)
        except ValueError:
            raise HTTPException(422, f"Invalid vt_symbol: {vt_symbol}") from None
        main_engine.subscribe(
            SubscribeRequest(symbol=symbol, exchange=exchange), gateway_name
        )
        subscribed.append(vt_symbol)
    return {"accepted": True, "vt_symbols": subscribed}
