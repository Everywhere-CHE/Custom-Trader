"""期权数据底座：fuyao 品种/合约目录同步 + 惰性详情与日K补全。

数据能力（探针实测，2026-11）：
- 合约目录仅含**在市**合约（无已到期合约），故回测窗口 =
  在市合约的生命周期（最长约 7 个月）；
- 期权日K：缺省参数仅最近 100 根；带 ``start/end`` 可取**完整
  生命周期**，时间戳为交易日**当日**北京零点（与股票"次日零点"
  约定不同，注意归一化差异）；
- 标的日线：ETF 走 ``/api/fund/market/historical``（date_ms 为
  次日零点约定）；指数走 ``/api/a-share/prices/historical``。

同步策略（分三级，控制请求量）：
- ``varieties``：品种资料，1 次调用；
- ``contracts``：合约目录全量分页（~2.8 万条，29 次调用），
  行权价/类型优先由 ticker 结构解析（CFFEX ``IO2612-C-4000``、
  ETF ``510050C2612M02800``），解析失败留待详情补全；
- ``details``：逐合约详情（行权价/类型/合约单位/保证金率/标的），
  请求量大（每标的上百次），默认只按需同步——链查询/回测时
  惰性补全，或显式指定 ``underlying`` 后台批量补全。

限流：令牌间隔 5 req/s 自适应，遇 4001/429 退避降速（与
:mod:`app.services.market_sync` 同一套参数）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import DATA_DIR
from ..core.constant import Exchange, Interval
from ..core.object import BarData
from ..core.utility import CHINA_TZ
from ..fuyao_config import load_fuyao_config

STATE_FILE = DATA_DIR / "option_sync_state.json"

BASE_URL = "https://fuyao.aicubes.cn"
VARIETIES_URL = f"{BASE_URL}/api/options/varieties/list"
CONTRACTS_LIST_URL = f"{BASE_URL}/api/options/contracts/list"
CONTRACTS_DETAIL_URL = f"{BASE_URL}/api/options/contracts/detail"
OPTION_DAILY_URL = f"{BASE_URL}/api/options/prices/daily"
FUND_HISTORICAL_URL = f"{BASE_URL}/api/fund/market/historical"
A_SHARE_HISTORICAL_URL = f"{BASE_URL}/api/a-share/prices/historical"
TICKER_SEARCH_URL = f"{BASE_URL}/api/meta/tickers/search"

# fuyao 交易所代码 -> 本地 Exchange
EXCHANGE_BY_CODE: dict[str, Exchange] = {
    "SSE": Exchange.SSE,
    "SZSE": Exchange.SZSE,
    "CFFEX": Exchange.CFFEX,
    "SHFE": Exchange.SHFE,
    "CZCE": Exchange.CZCE,
    "DCE": Exchange.DCE,
    "INE": Exchange.INE,
    "GFEX": Exchange.GFEX,
}

# 同花顺指数代码 -> 本地指数 thscode（CFFEX 期权标的）
THS_INDEX_MAP: dict[str, str] = {
    "1B0016": "000016.SH",  # 上证50
    "1B0300": "000300.SH",  # 沪深300
    "1B0905": "000905.SH",  # 中证500
    "1B0852": "000852.SH",  # 中证1000
}

# CFFEX 品种 -> 默认标的信息（详情同步前的合理默认）
CFFEX_DEFAULTS: dict[str, dict[str, Any]] = {
    "HO": {"multiplier": 100.0, "underlying_ths": "000016.SH"},
    "IO": {"multiplier": 100.0, "underlying_ths": "000300.SH"},
    "MO": {"multiplier": 100.0, "underlying_ths": "000852.SH"},
}

# 限流参数（与 market_sync 保持一致）
DEFAULT_RATE = 5.0
MIN_RATE = 1.5
BACKOFF_ON_429 = 4.0
MAX_RETRIES = 5

# 日K分片：单请求最多约 120 个自然日（远超100根上限保护）
BAR_CHUNK_DAYS = 120

# ticker 解析
_RE_CFFEX = re.compile(r"^([A-Z]+)(\d{4})-([CP])-(\d+(?:\.\d+)?)$")
_RE_ETF = re.compile(r"^(\d{6})([CP])(\d{2})(\d{2})[A-Z]?(\d+)$")


DETAILS_CKPT_FILE = DATA_DIR / "option_sync_details_ckpt.json"


def _load_details_ckpt() -> set[str]:
    try:
        if DETAILS_CKPT_FILE.is_file():
            return set(json.loads(DETAILS_CKPT_FILE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass
    return set()


def _save_details_ckpt(done: set[str]) -> None:
    DETAILS_CKPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DETAILS_CKPT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(done)), encoding="utf-8")
    tmp.replace(DETAILS_CKPT_FILE)


class OptionSyncError(RuntimeError):
    """期权同步/取数失败。"""


def _load_state() -> dict:
    try:
        if STATE_FILE.is_file():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(STATE_FILE)


def make_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"X-api-key": api_key},
        timeout=25,
    )


def make_rate_box() -> dict:
    return {"rate": DEFAULT_RATE, "next_time": 0.0}


def load_api_key() -> str:
    """读取 fuyao API Key（运行时配置优先，缺省回退 .env）。"""

    class _S:  # load_fuyao_config 只读默认时需要的占位对象
        fuyao_api_key = ""
        fuyao_poll_interval = 5.0
        fuyao_enable_quotes = False

    return str(load_fuyao_config(_S()).get("api_key") or "")


def get_item(
    client: httpx.Client,
    url: str,
    params: dict,
    rate_box: dict,
    tolerate_codes: set[int] | None = None,
) -> dict:
    """带限流/退避的 GET，返回信封 data；业务错误抛 OptionSyncError。

    ``tolerate_codes``：视为"无数据"的业务码（返回空 data 不抛错），
    如 5003（该合约无日K行情，新挂/无成交合约常见）。
    """
    retries = 0
    while True:
        wait = rate_box["next_time"] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        rate_box["next_time"] = time.monotonic() + 1.0 / rate_box["rate"]

        resp = client.get(url, params=params)
        try:
            envelope = resp.json()
        except ValueError:
            raise OptionSyncError(
                f"非 JSON 响应 HTTP {resp.status_code}"
            ) from None
        code = envelope.get("code")
        if code in (0, 200):
            rate_box["rate"] = min(DEFAULT_RATE, rate_box["rate"] * 1.05)
            return envelope.get("data") or {}
        if tolerate_codes and code in tolerate_codes:
            return {}
        if code == 4001 or resp.status_code == 429:
            retries += 1
            if retries > MAX_RETRIES:
                raise OptionSyncError(
                    f"限流重试耗尽（{MAX_RETRIES} 次）: {url}"
                )
            rate_box["rate"] = max(MIN_RATE, rate_box["rate"] * 0.5)
            time.sleep(BACKOFF_ON_429 * retries)
            continue
        raise OptionSyncError(
            f"fuyao code={code}: {str(envelope.get('message'))[:80]}"
        )


# ----------------------------------------------------------------------
# ticker 解析与标的解析
# ----------------------------------------------------------------------

def parse_option_ticker(ticker: str, exchange_code: str) -> dict | None:
    """从 ticker 结构解析行权价/类型/标的；不支持的格式返回 None。

    - CFFEX：``IO2612-C-4000`` → variety=IO, 2612, call, 4000
    - ETF：``510050C2612M02800`` → 510050, call, 2612, 2.800
    """
    if not ticker:
        return None
    if exchange_code == "CFFEX":
        m = _RE_CFFEX.match(ticker)
        if not m:
            return None
        return {
            "underlying_code": None,  # 详情补全（1B0300 等）
            "option_type": "call" if m.group(3) == "C" else "put",
            "strike_price": float(m.group(4)),
            "yymm": m.group(2),
        }
    m = _RE_ETF.match(ticker)
    if m:
        return {
            "underlying_code": m.group(1),
            "option_type": "call" if m.group(2) == "C" else "put",
            "strike_price": int(m.group(5)) / 1000.0,
            "yymm": m.group(3) + m.group(4),
        }
    return None


def resolve_underlying_thscode(
    client: httpx.Client,
    rate_box: dict,
    underlying_code: str | None,
    underlying_id: str,
    exchange_code: str,
) -> str:
    """把"标的标识"解析为可查日线的 thscode。

    - ETF：``510050`` → ``510050.SH``（按品种交易所定后缀）
    - CFFEX：``IO`` → 详情标的 ``1B0300`` → ``000300.SH``，
      映射表未命中时走 ticker-search 动态解析
    """
    if underlying_id.isdigit():
        return underlying_id + (".SZ" if exchange_code == "SZSE" else ".SH")
    ths = None
    if underlying_code:
        ths = THS_INDEX_MAP.get(underlying_code)
    if not ths:
        # CFFEX 默认映射
        ths = (CFFEX_DEFAULTS.get(underlying_id) or {}).get("underlying_ths")
    if not ths and underlying_code:
        data = get_item(
            client,
            TICKER_SEARCH_URL,
            {"q": underlying_code, "limit": 5},
            rate_box,
        )
        for it in data.get("item") or []:
            if (it.get("asset_type") or "").startswith("a-share-index"):
                ths = it.get("thscode")
                break
    return ths or underlying_id


def underlying_variety_id(underlying_id: str) -> str:
    """标的标识 -> 期权品种代码（510050 → 510050O，IO → IO）。"""
    return underlying_id if not underlying_id.isdigit() else underlying_id + "O"


# ----------------------------------------------------------------------
# 日K拉取（归一化到交易日 00:00，naive Beijing）
# ----------------------------------------------------------------------

def _norm_option_day(ts_ms: int) -> datetime:
    """期权日K时间戳 = 交易日**当日**北京零点。"""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=CHINA_TZ)
    return dt.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def _norm_next_day(ts_ms: int) -> datetime:
    """股票/基金 date_ms = 交易日**次日**北京零点（+8h 归一）。"""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) + timedelta(
        hours=8
    )
    return dt.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def _bar(thscode: str, exchange: Exchange, dt: datetime, row: dict) -> BarData:
    return BarData(
        gateway_name="FUYAO",
        symbol=thscode,
        exchange=exchange,
        datetime=dt,
        interval=Interval.DAILY,
        open_price=float(row.get("open_price") or 0),
        close_price=float(row.get("close_price") or 0),
        high_price=float(row.get("high_price") or 0),
        low_price=float(row.get("low_price") or 0),
        volume=float(row.get("volume") or 0),
        turnover=float(row.get("turnover") or 0),
    )


def fetch_option_daily(
    client: httpx.Client,
    rate_box: dict,
    thscode: str,
    exchange: Exchange,
    start: datetime,
    end: datetime,
) -> list[BarData]:
    """期权日K（分片 ≤120 自然日，归一化交易日 00:00，升序）。"""
    bars: list[BarData] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=BAR_CHUNK_DAYS), end)
        data = get_item(
            client,
            OPTION_DAILY_URL,
            {
                "thscode": thscode,
                "start": int(chunk_start.timestamp() * 1000),
                "end": int(chunk_end.timestamp() * 1000),
            },
            rate_box,
            tolerate_codes={5003},  # 无日K行情（新挂/无成交合约）
        )
        for row in data.get("item") or []:
            ts = row.get("timestamp")
            if not ts:
                continue
            bars.append(_bar(thscode, exchange, _norm_option_day(ts), row))
        chunk_start = chunk_end + timedelta(days=1)
    bars.sort(key=lambda b: b.datetime)
    return bars


def fetch_underlying_daily(
    client: httpx.Client,
    rate_box: dict,
    thscode: str,
    exchange: Exchange,
    start: datetime,
    end: datetime,
    is_etf: bool,
) -> list[BarData]:
    """标的日线：ETF 走基金接口，指数走 A 股接口（均次日零点约定）。"""
    url = FUND_HISTORICAL_URL if is_etf else A_SHARE_HISTORICAL_URL
    params: dict = {
        "thscode": thscode,
        "start": int(start.timestamp() * 1000),
        "end": int(end.timestamp() * 1000),
    }
    if not is_etf:
        params["interval"] = "1d"
        params["adjust"] = "forward"
    data = get_item(client, url, params, rate_box)
    bars: list[BarData] = []
    for row in data.get("item") or []:
        ts = row.get("date_ms")
        if not ts:
            continue
        bars.append(_bar(thscode, exchange, _norm_next_day(ts), row))
    bars.sort(key=lambda b: b.datetime)
    return bars


# ----------------------------------------------------------------------
# 惰性补全（链查询 / 回测使用）
# ----------------------------------------------------------------------

def ensure_contract_details(
    ths_codes: list[str],
    progress=None,  # noqa: ANN001
) -> int:
    """批量补全合约详情（跳过已补全的），返回本次补全条数。"""
    from sqlalchemy import select

    from ..db.models import OptionContractModel
    from ..db.session import create_session

    todo = ths_codes
    if todo:
        session = create_session()
        try:
            done = set(
                session.execute(
                    select(OptionContractModel.thscode).where(
                        OptionContractModel.thscode.in_(todo),
                        OptionContractModel.details_fetched.is_(True),
                    )
                ).scalars()
            )
        finally:
            session.close()
        todo = [c for c in ths_codes if c not in done]
    if not todo:
        return 0

    api_key = load_api_key()
    if not api_key:
        raise OptionSyncError("未配置 FUYAO API Key（设置页 → 数据源）")
    client = make_client(api_key)
    rate_box = make_rate_box()
    fetched = 0
    try:
        for i, thscode in enumerate(todo):
            data = get_item(
                client,
                CONTRACTS_DETAIL_URL,
                {"thscode": thscode},
                rate_box,
            )
            _save_contract_detail(data)
            fetched += 1
            if progress and (i % 20 == 0 or i == len(todo) - 1):
                progress(f"补全详情 {i + 1}/{len(todo)}")
    finally:
        client.close()
    return fetched


def _save_contract_detail(data: dict) -> None:
    """把单条详情 upsert 进 option_contracts。"""
    from ..db.models import OptionContractModel
    from ..db.session import create_session

    thscode = data.get("thscode")
    if not thscode:
        return
    row = {
        "thscode": thscode,
        "ticker": data.get("ticker") or "",
        "name": data.get("name") or "",
        # 详情接口不含交易所，沿用目录行已有值，缺省按代码后缀推断
        "exchange_code": "",
        "variety_code": data.get("variety_code") or "",
        "option_type": data.get("option_type"),
        "strike_price": data.get("strike_price"),
        "underlying_code": data.get("underlying_code"),
        "exercise_style": data.get("exercise_style"),
        "trade_amount": data.get("trade_amount"),
        "margin_rate": data.get("margin_rate"),
        "list_date": data.get("list_date"),
        "end_date": data.get("end_date"),
        "last_trade_date": data.get("last_trade_date"),
        "details_fetched": True,
    }
    # exchange_code 以目录行已有值为准（详情不含），只在缺时用后缀推断
    session = create_session()
    try:
        existing = (
            session.query(OptionContractModel)
            .filter(OptionContractModel.thscode == thscode)
            .first()
        )
        if existing is not None:
            row["exchange_code"] = (
                existing.exchange_code
                or _exchange_from_thscode(thscode).value
            )
            for k, v in row.items():
                setattr(existing, k, v)
        else:
            row["exchange_code"] = _exchange_from_thscode(thscode).value
            session.add(OptionContractModel(**row))
        session.commit()
    finally:
        session.close()


# 期权 thscode 市场后缀（与期货后缀一致）
OPTION_SUFFIX_BY_EXCHANGE: dict[str, str] = {
    "CZCE": "CZC", "SHFE": "SHF", "DCE": "DCE", "GFEX": "GFE",
    "INE": "INE", "CFFEX": "CFE",
}


def resolve_option_thscode(symbol: str, exchange_code: str) -> str | None:
    """CTP 期权代码 → fuyao thscode。

    优先查本地合约目录（ticker 精确匹配，如 MA701P2800@CZCE →
    MA701P2800.CZC）；目录未命中按交易所后缀兜底。
    """
    symbol = symbol.strip()
    # 变体：CTP 风格 i2701C700 ↔ fuyao 横线大写 I2701-C-700
    import re

    m = re.match(r"^([A-Za-z]+\d+)([CP])(\d+)$", symbol)
    dashed = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    candidates = [symbol, symbol.upper()]
    if dashed:
        candidates += [dashed, dashed.upper()]
    try:
        from sqlalchemy import select

        from ..db.models import OptionContractModel
        from ..db.session import create_session

        session = create_session()
        try:
            row = session.execute(
                select(OptionContractModel.thscode).where(
                    OptionContractModel.ticker.in_(
                        [c for c in candidates if c]
                    ),
                    OptionContractModel.exchange_code == exchange_code,
                )
            ).first()
        finally:
            session.close()
        if row and row[0]:
            return row[0]
    except Exception:  # noqa: BLE001
        pass
    suffix = OPTION_SUFFIX_BY_EXCHANGE.get(exchange_code)
    return f"{symbol}.{suffix}" if suffix else None


def ensure_option_bars_for_symbol(
    data_engine,  # noqa: ANN001
    symbol: str,
    exchange_code: str,
    start: datetime,
    end: datetime,
) -> list[BarData]:
    """按 CTP 期权代码补拉日K（供 /api/bars 与关键价位兜底）。"""
    thscode = resolve_option_thscode(symbol, exchange_code)
    if not thscode:
        return []
    exchange = EXCHANGE_BY_CODE.get(exchange_code)
    if exchange is None:
        return []
    return ensure_option_bars(data_engine, thscode, start, end)


def _exchange_from_thscode(thscode: str) -> Exchange:
    suffix = thscode.rsplit(".", 1)[-1].upper() if "." in thscode else ""
    return {
        # 期权 thscode 后缀（含商品期权）
        "SH": Exchange.SSE,
        "SZ": Exchange.SZSE,
        "CFE": Exchange.CFFEX,
        "CZC": Exchange.CZCE,
        "SHF": Exchange.SHFE,
        "DCE": Exchange.DCE,
        "GFE": Exchange.GFEX,
        "INE": Exchange.INE,
    }.get(suffix, Exchange.LOCAL)


def ensure_option_bars(
    data_engine,  # noqa: ANN001
    thscode: str,
    start: datetime,
    end: datetime,
    client: httpx.Client | None = None,
    rate_box: dict | None = None,
) -> list[BarData]:
    """确保 [start, end] 期权日K已入库，返回区间内日线（升序）。

    本地已覆盖时直接读；缺失时拉取并 ``save_bars`` 幂等回填。
    """
    from ..db.data_engine import DataEngine
    from ..db.models import OptionContractModel
    from ..db.session import create_session

    if not isinstance(data_engine, DataEngine):
        raise OptionSyncError("DataEngine not available")
    session = create_session()
    try:
        row = (
            session.query(OptionContractModel)
            .filter(OptionContractModel.thscode == thscode)
            .first()
        )
    finally:
        session.close()
    if row is None:
        raise OptionSyncError(f"合约不在目录中: {thscode}（请先同步合约）")
    exchange = EXCHANGE_BY_CODE.get(row.exchange_code)
    if exchange is None:
        exchange = _exchange_from_thscode(thscode)

    # 本地期权日线统一以 thscode 作为 bars.symbol 存取
    local = data_engine.load_bars(
        thscode, exchange, Interval.DAILY, start, end
    )
    if not _covers(local, start, end):
        own_client = client is None
        if own_client:
            api_key = load_api_key()
            if not api_key:
                raise OptionSyncError("未配置 FUYAO API Key（设置页 → 数据源）")
            client = make_client(api_key)
            rate_box = make_rate_box()
        try:
            bars = fetch_option_daily(
                client, rate_box, thscode, exchange, start, end
            )
            if bars:
                data_engine.save_bars(bars)
            local = data_engine.load_bars(
                thscode, exchange, Interval.DAILY, start, end
            ) or bars
        finally:
            if own_client:
                client.close()
    return local


def _covers(bars: list[BarData], start: datetime, end: datetime) -> bool:
    """判断本地日线是否已覆盖 [start, end]（按交易日近似：首尾夹住即可）。"""
    if not bars:
        return False
    return bars[0].datetime <= start + timedelta(days=7) and bars[
        -1
    ].datetime >= end - timedelta(days=7)


def ensure_underlying_bars(
    data_engine,  # noqa: ANN001
    underlying_id: str,
    exchange_code: str,
    underlying_code: str | None,
    start: datetime,
    end: datetime,
) -> list[BarData]:
    """确保标的日线已入库并返回（ETF 基金接口 / 指数 A 股接口）。"""
    from ..db.data_engine import DataEngine

    if not isinstance(data_engine, DataEngine):
        raise OptionSyncError("DataEngine not available")
    if underlying_id.isdigit():
        thscode = underlying_id + (
            ".SZ" if exchange_code == "SZSE" else ".SH"
        )
        exchange = (
            Exchange.SZSE if exchange_code == "SZSE" else Exchange.SSE
        )
        is_etf = True
    else:
        defaults = CFFEX_DEFAULTS.get(underlying_id) or {}
        thscode = THS_INDEX_MAP.get(underlying_code or "") or defaults.get(
            "underlying_ths"
        )
        if not thscode:
            api_key = load_api_key()
            if not api_key:
                raise OptionSyncError("未配置 FUYAO API Key（设置页 → 数据源）")
            client = make_client(api_key)
            rate_box = make_rate_box()
            try:
                thscode = resolve_underlying_thscode(
                    client,
                    rate_box,
                    underlying_code,
                    underlying_id,
                    exchange_code,
                )
            finally:
                client.close()
        if not thscode:
            raise OptionSyncError(f"无法解析指数标的: {underlying_id}")
        exchange = Exchange.SSE
        is_etf = False

    local = data_engine.load_bars(
        thscode, exchange, Interval.DAILY, start, end
    )
    if _covers(local, start, end):
        return local

    api_key = load_api_key()
    if not api_key:
        raise OptionSyncError("未配置 FUYAO API Key（设置页 → 数据源）")
    client = make_client(api_key)
    rate_box = make_rate_box()
    try:
        bars = fetch_underlying_daily(
            client, rate_box, thscode, exchange, start, end, is_etf
        )
        if bars:
            data_engine.save_bars(bars)
        return data_engine.load_bars(
            thscode, exchange, Interval.DAILY, start, end
        ) or bars
    finally:
        client.close()


# ----------------------------------------------------------------------
# 后台同步管理器（品种 / 合约目录 / 指定标的详情）
# ----------------------------------------------------------------------

class OptionSyncManager:
    """单飞后台同步：varieties / contracts / details。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "status": "idle",
            "scope": "",
            "progress": "",
            "total_contracts": 0,
            "saved_contracts": 0,
            "detail_done": 0,
            "detail_total": 0,
            "errors": 0,
            "started_at": None,
            "finished_at": None,
            "error": "",
        }
        self._thread: threading.Thread | None = None
        self._persisted = _load_state()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state["status"] == "running"

    def status(self) -> dict:
        with self._lock:
            state = dict(self._state)
        persisted = dict(self._persisted)
        return {
            **state,
            "last_sync": persisted.get("last_sync"),
            "contract_count": persisted.get("contract_count"),
        }

    def _update(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

    def start(self, scope: str, underlying: str | None = None) -> bool:
        """启动后台同步；已有任务在跑时返回 False。"""
        if self.running or scope not in (
            "varieties",
            "contracts",
            "details",
            "all",
        ):
            return False
        self._update(
            status="running",
            scope=scope,
            progress="启动…",
            total_contracts=0,
            saved_contracts=0,
            detail_done=0,
            detail_total=0,
            errors=0,
            started_at=datetime.now(CHINA_TZ).isoformat(),
            finished_at=None,
            error="",
        )
        self._thread = threading.Thread(
            target=self._run,
            args=(scope, underlying),
            name="OptionSync",
            daemon=True,
        )
        self._thread.start()
        return True

    # ------------------------------------------------------------------

    def _run(self, scope: str, underlying: str | None) -> None:
        try:
            api_key = load_api_key()
            if not api_key:
                raise OptionSyncError("未配置 FUYAO API Key（设置页 → 数据源）")
            client = make_client(api_key)
            rate_box = make_rate_box()
            try:
                if scope in ("varieties", "all"):
                    self._sync_varieties(client, rate_box)
                if scope in ("contracts", "all"):
                    self._sync_contracts(client, rate_box)
                if scope == "details":
                    self._sync_details(client, rate_box, underlying)
            finally:
                client.close()
            self._persist()
            self._update(
                status="done",
                progress="完成",
                finished_at=datetime.now(CHINA_TZ).isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            self._update(
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(CHINA_TZ).isoformat(),
            )

    def _persist(self) -> None:
        with self._lock:
            state = dict(self._state)
        self._persisted = {
            "last_sync": datetime.now(CHINA_TZ).isoformat(),
            "contract_count": state.get("total_contracts"),
        }
        _save_state(self._persisted)

    # ------------------------------------------------------------------

    def _sync_varieties(self, client: httpx.Client, rate_box: dict) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from ..db.models import OptionVarietyModel
        from ..db.session import create_session, get_engine

        is_sqlite = str(get_engine().url).startswith("sqlite")
        self._update(progress="同步品种资料…")
        data = get_item(client, VARIETIES_URL, {}, rate_box)
        rows: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for it in data.get("item") or []:
            code = it.get("variety_code")
            exch = it.get("exchange_code") or ""
            if not code or (code, exch) in seen:
                continue
            seen.add((code, exch))
            rows.append(
                {
                    "variety_code": code,
                    "exchange_code": exch,
                    "name": it.get("name") or "",
                    "margin_rate": it.get("margin_rate"),
                    "trade_amount": it.get("trade_amount"),
                    "contract_multiplier": it.get("contract_multiplier"),
                    "tick_size": it.get("tick_size"),
                    "transaction_fee": it.get("transaction_fee"),
                    "exercise_fee": it.get("exercise_fee"),
                    "settlement_type": it.get("settlement_type"),
                }
            )
        session = create_session()
        try:
            for row in rows:
                stmt = sqlite_insert(OptionVarietyModel).values(**row)
                if not is_sqlite:
                    from sqlalchemy.dialects.postgresql import (
                        insert as pg_insert,
                    )

                    stmt = pg_insert(OptionVarietyModel).values(**row)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["variety_code", "exchange_code"],
                    set_={k: v for k, v in row.items()},
                )
                session.execute(stmt)
            session.commit()
        finally:
            session.close()
        self._update(progress=f"品种资料 {len(rows)} 条")

    def _sync_contracts(self, client: httpx.Client, rate_box: dict) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from ..db.models import OptionContractModel
        from ..db.session import create_session, get_engine

        is_sqlite = str(get_engine().url).startswith("sqlite")
        self._update(progress="同步合约目录（分页）…")
        offset = 0
        total = 0
        batch: list[dict] = []

        def _flush() -> None:
            nonlocal batch
            if not batch:
                return
            session = create_session()
            try:
                for row in batch:
                    stmt = sqlite_insert(OptionContractModel).values(**row)
                    if not is_sqlite:
                        from sqlalchemy.dialects.postgresql import (
                            insert as pg_insert,
                        )

                        stmt = pg_insert(OptionContractModel).values(**row)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["thscode"],
                        set_={
                            k: v
                            for k, v in row.items()
                            if k != "thscode"
                            # 已补全的详情不被目录行覆盖
                            and not (
                                k == "details_fetched"
                                and v is False
                            )
                        },
                    )
                    session.execute(stmt)
                session.commit()
            finally:
                session.close()
            batch = []

        while True:
            data = get_item(
                client,
                CONTRACTS_LIST_URL,
                {"limit": 1000, "offset": offset},
                rate_box,
            )
            items = data.get("item") or []
            if not items:
                break
            for it in items:
                thscode = it.get("thscode")
                if not thscode:
                    continue
                exchange_code = it.get("exchange_code") or ""
                parsed = parse_option_ticker(
                    it.get("ticker") or "", exchange_code
                )
                batch.append(
                    {
                        "thscode": thscode,
                        "ticker": it.get("ticker") or "",
                        "name": it.get("name") or "",
                        "exchange_code": exchange_code,
                        "variety_code": it.get("variety_code") or "",
                        "option_type": (parsed or {}).get("option_type"),
                        "strike_price": (parsed or {}).get("strike_price"),
                        "underlying_code": (parsed or {}).get(
                            "underlying_code"
                        ),
                        "list_date": it.get("list_date"),
                        "end_date": it.get("end_date"),
                        "last_trade_date": it.get("last_trade_date"),
                        "details_fetched": False,
                    }
                )
                total += 1
                if len(batch) >= 1000:
                    _flush()
                    self._update(
                        total_contracts=total,
                        saved_contracts=total,
                        progress=f"合约目录 {total}…",
                    )
            offset += len(items)
            if len(items) < 1000:
                break
        _flush()
        self._update(
            total_contracts=total,
            saved_contracts=total,
            progress=f"合约目录完成 {total} 条",
        )

    def _sync_details(
        self,
        client: httpx.Client,
        rate_box: dict,
        underlying: str | None,
    ) -> None:
        """批量补全详情（带断点续传）。

        默认目标 = 已解析合约中尚未补详情的（ETF/CFFEX 约 2000+
        条，商品期权 ticker 解析不出不纳入，避免 2.6 万次请求）；
        传 ``underlying`` 则限定单标的。断点存
        ``data/option_sync_details_ckpt.json``，中断重跑自动跳过。
        """
        from sqlalchemy import select

        from ..db.models import OptionContractModel
        from ..db.session import create_session

        filters = [OptionContractModel.details_fetched.is_(False)]
        if underlying:
            filters.append(
                OptionContractModel.variety_code
                == underlying_variety_id(underlying)
            )
        else:
            # 默认只补"已解析出行权价"的合约（ticker 解析成功的
            # ETF/CFFEX），商品期权数量过大且非本期目标
            filters.append(OptionContractModel.strike_price.isnot(None))
        session = create_session()
        try:
            codes = list(
                session.execute(
                    select(OptionContractModel.thscode).where(*filters)
                ).scalars()
            )
        finally:
            session.close()

        ckpt = _load_details_ckpt()
        todo = [c for c in codes if c not in ckpt]
        self._update(
            detail_total=len(todo),
            progress=f"待补详情 {len(todo)}（断点已跳过 {len(codes) - len(todo)}）",
        )
        done = 0
        for i, thscode in enumerate(todo):
            data = get_item(
                client, CONTRACTS_DETAIL_URL, {"thscode": thscode}, rate_box
            )
            _save_contract_detail(data)
            ckpt.add(thscode)
            done += 1
            if done % 25 == 0:
                _save_details_ckpt(ckpt)
            if i % 20 == 0 or i == len(todo) - 1:
                self._update(
                    detail_done=done, progress=f"详情 {done}/{len(todo)}"
                )
        _save_details_ckpt(ckpt)


# 模块级单例
option_sync_manager = OptionSyncManager()
