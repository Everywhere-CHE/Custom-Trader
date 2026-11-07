"""FUYAO 网关：同花顺金融数据 API（https://fuyao.aicubes.cn）。

定位：高质量 A 股历史日线数据源（前复权），并可选提供行情快照轮询。
认证方式为请求头 ``X-api-key``（在设置页配置，持久化到
data/fuyao_config.json）；响应为统一信封 ``{code, message, data}``，
业务错误以 HTTP 200 + code 表达（2001 Key 无效、2003 无权限、
4001/429 限流、1003 时间跨度越界等）。

连接时自动校验 API Key（调用交易日历接口）；未配置 Key 时连接
失败并在日志中给出明确指引。历史 K 线仅支持日线（interval=1d，
单次跨度上限 10 年），周线及以上由上层用日线聚合或回退其它网关。
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import requests

from ...constant import Direction, Exchange, Interval, Product
from ...event import EventEngine
from ...utility import extract_vt_symbol
from ...object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderRequest,
    SubscribeRequest,
    TickData,
)
from ..base import BaseGateway

BASE_URL = "https://fuyao.aicubes.cn"
SNAPSHOT_URL = f"{BASE_URL}/api/a-share/prices/snapshot"
HISTORICAL_URL = f"{BASE_URL}/api/a-share/prices/historical"
CALENDAR_URL = f"{BASE_URL}/api/a-share/calendar/trading-days"
FUTURES_DAILY_URL = f"{BASE_URL}/api/futures/prices/daily"
FUTURES_INTRADAY_URL = f"{BASE_URL}/api/futures/prices/intraday"
OPTIONS_INTRADAY_URL = f"{BASE_URL}/api/options/prices/intraday"
FUTURES_CONTRACTS_URL = f"{BASE_URL}/api/futures/contracts/list"
TICKER_SEARCH_URL = f"{BASE_URL}/api/meta/tickers/search"

# 连接自检 / 历史请求的最大时间跨度保护（毫秒）
MAX_HISTORY_SPAN_MS = 10 * 365 * 24 * 3600 * 1000 - 24 * 3600 * 1000

EXCHANGE_SUFFIX: dict[Exchange, str] = {
    Exchange.SSE: ".SH",
    Exchange.SZSE: ".SZ",
    Exchange.BSE: ".BJ",
}

# fuyao 期货 thscode 后缀与 CTP 交易所代码不同（SHFE→SHF、
# CFFEX→CFE 等），先按映射直连，失败再经 ticker-search 动态解析。
FUTURES_EXCHANGES: frozenset[Exchange] = frozenset({
    Exchange.CFFEX, Exchange.SHFE, Exchange.CZCE,
    Exchange.DCE, Exchange.INE, Exchange.GFEX,
})
FUTURES_SUFFIX: dict[Exchange, str] = {
    Exchange.CFFEX: "CFE",
    Exchange.SHFE: "SHF",
    Exchange.CZCE: "CZC",
    Exchange.DCE: "DCE",
    Exchange.INE: "INE",
    Exchange.GFEX: "GFE",
}

# fuyao 合约表的交易所代码 -> 本地 Exchange
EXCHANGE_BY_CODE: dict[str, Exchange] = {
    "CFFEX": Exchange.CFFEX, "SHFE": Exchange.SHFE, "CZCE": Exchange.CZCE,
    "DCE": Exchange.DCE, "INE": Exchange.INE, "GFEX": Exchange.GFEX,
}

# 网关业务错误码 → 可读信息（用于自动调试输出）
ERROR_HINTS: dict[int, str] = {
    1001: "缺少必填参数",
    1002: "参数格式错误",
    1003: "参数越界（如时间跨度超过10年）",
    2001: "API Key 缺失或无效",
    2003: "无权访问该数据能力",
    3001: "标的不存在",
    4001: "触发限流，请降低请求频率",
    5001: "服务端内部错误",
}


class FuyaoApiError(Exception):
    """fuyao 业务错误（信封 code != 0）。"""

    def __init__(self, code: int, message: str) -> None:
        hint = ERROR_HINTS.get(code, "")
        text = f"code={code} {message}" + (f"（{hint}）" if hint else "")
        super().__init__(text)
        self.code = code


def to_thscode(symbol: str, exchange: Exchange) -> str:
    """把 (symbol, exchange) 转换为同花顺 thscode，如 ``600519.SH``。"""
    return f"{symbol}{EXCHANGE_SUFFIX.get(exchange, '.SH')}"


class FuyaoGateway(BaseGateway):
    """同花顺金融数据 API 网关（数据源：日线历史 + 可选快照行情）。"""

    default_name = "FUYAO"

    default_setting: dict[str, str | int | float | bool] = {
        "api_key": "",
        "poll_interval": 5.0,
        "enable_quotes": False,
    }

    exchanges: list[Exchange] = [
        Exchange.SSE, Exchange.SZSE, Exchange.BSE,
        Exchange.CFFEX, Exchange.SHFE, Exchange.CZCE,
        Exchange.DCE, Exchange.INE, Exchange.GFEX,
    ]
    # 纯历史/分时数据网关：不能交付实时 tick，订阅路由应最后考虑
    data_only = True

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)

        self._lock = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None
        self._poll_interval = 5.0
        self._enable_quotes = False

        self._session = requests.Session()
        self._session.headers.update({"X-api-key": ""})
        self._contracts: dict[str, ContractData] = {}   # vt_symbol -> 合约
        self._subscribed: dict[str, str] = {}           # thscode -> vt_symbol（期货）
        self._option_subscribed: dict[str, str] = {}    # thscode -> vt_symbol（期权）
        self._last_option_ts: dict[str, int] = {}
        self._last_snapshot_ts: int | None = None       # 快照批时间戳去重
        self._last_futures_ts: dict[str, object] = {}   # 期货分时最新点去重
        self._ths_cache: dict[str, str | None] = {}     # CTP代码 -> 期货thscode（负缓存防重复查询）

    # ------------------------------------------------------------------
    # 内部 API 访问
    # ------------------------------------------------------------------

    def _set_key(self, api_key: str) -> None:
        self._session.headers.update({"X-api-key": api_key.strip()})

    def _request(self, url: str, params: dict | None = None,
                 timeout: float = 10) -> dict:
        """GET 请求并解析统一信封；code != 0 抛 FuyaoApiError。"""
        resp = self._session.get(url, params=params or {}, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        code = payload.get("code", -1)
        if code != 0:
            raise FuyaoApiError(int(code), str(payload.get("message", "")))
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # BaseGateway 接口实现
    # ------------------------------------------------------------------

    def connect(self, setting: dict) -> None:
        if self.connected:
            self.write_log("FUYAO gateway already connected")
            return

        api_key = str(setting.get("api_key", "") or "").strip()
        if not api_key:
            self.write_log(
                "FUYAO 连接失败：未配置 API Key。请在「设置 → 网关管理 → "
                "FUYAO 数据源设置」中填写同花顺金融数据 API Key"
                "（fuyao.aicubes.cn /admin/ 页签发）",
                level=logging.ERROR,
            )
            return

        self._set_key(api_key)
        self._poll_interval = max(
            2.0,
            float(setting.get("poll_interval",
                              self.default_setting["poll_interval"])),
        )
        self._enable_quotes = bool(setting.get("enable_quotes", False))

        # 连接自检：用交易日历接口校验 Key 有效性（自动判断调试）
        try:
            started = time.perf_counter()
            data = self._request(CALENDAR_URL, timeout=10)
            latency_ms = int((time.perf_counter() - started) * 1000)
            days = len(data.get("item") or [])
            self.write_log(
                f"FUYAO 自检通过：Key 有效，交易日历返回 {days} 个交易日，"
                f"延迟 {latency_ms}ms"
            )
        except FuyaoApiError as e:
            self.write_log(
                f"FUYAO 连接失败：API Key 校验未通过 —— {e}",
                level=logging.ERROR,
            )
            return
        except Exception as e:
            self.write_log(
                f"FUYAO 连接失败：无法访问 {BASE_URL} —— {e}",
                level=logging.ERROR,
            )
            return

        self.connected = True
        self.on_gateway_status()
        self.write_log(
            f"FUYAO gateway connected (同花顺金融数据 API, "
            f"ashare snapshot {'on' if self._enable_quotes else 'off'}, "
            f"futures intraday poll {self._poll_interval}s)"
        )

        # 纯数据网关上报一个占位账户
        self.on_account(
            AccountData(
                gateway_name=self.gateway_name,
                accountid="data_source",
                balance=0,
            )
        )

        # 拉取期货合约表注册到 OMS：无 CTP 环境下期货合约也可检索/回测
        self._register_futures_contracts()
        self.write_log(f"Contract data received: {len(self._contracts)}")

        # 轮询线程常驻：期货分时轮询订阅即生效（无 CTP 时的实时来源）；
        # A 股快照批量轮询仍由 enable_quotes 开关控制（免费腾讯源已覆盖）
        self._active = True
        self._thread = threading.Thread(
            target=self._run_poll, name="FuyaoQuotePoll", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        if not self.connected:
            return
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.connected = False
        self.on_gateway_status()
        self.write_log("FUYAO gateway disconnected")

    def subscribe(self, req: SubscribeRequest) -> None:
        if req.exchange not in self.exchanges:
            self.write_log(
                f"Unsupported exchange for FUYAO gateway: {req.vt_symbol}",
                level=logging.WARNING,
            )
            return

        if self._is_option_symbol(req.symbol, req.exchange):
            # 期权：登记分时轮询（真实分钟价，延迟约一分钟）。
            # CTP 风格代码经本地期权目录解析为 fuyao thscode
            thscode = self._resolve_option_thscode(req.symbol, req.exchange.value)
            if not thscode:
                self.write_log(
                    f"FUYAO 期权代码解析失败: {req.vt_symbol}，跳过订阅",
                    level=logging.WARNING,
                )
                return
            with self._lock:
                self._option_subscribed[thscode] = req.vt_symbol
            self.write_log(
                f"Subscribed options intraday: {req.vt_symbol} ({thscode})"
            )
            return

        if req.exchange in FUTURES_EXCHANGES:
            # 期货：连接时已从合约表注册，这里登记分时轮询（分钟级实时，
            # 数据晚约一分钟；实时五档盘口仍建议 CTP）
            # 合约表注册的是 fuyao 大写 ticker（RB2701.SHFE），CTP 风格
            # 订阅是小写（rb2701.SHFE），按大写归一匹配
            contract = next(
                (c for k, c in self._contracts.items()
                 if k.upper() == req.vt_symbol.upper()),
                None,
            )
            if contract is None:
                self.write_log(
                    f"FUYAO 未收录合约 {req.vt_symbol}，跳过订阅",
                    level=logging.WARNING,
                )
                return
            thscode = self._resolve_futures_thscode(req.symbol, req.exchange)
            if not thscode:
                return
            with self._lock:
                self._subscribed[thscode] = req.vt_symbol
            self.write_log(
                f"Subscribed futures intraday: {req.vt_symbol} ({thscode})"
            )
            return

        if req.vt_symbol not in self._contracts:
            self._register_contract(req.symbol, req.exchange, req.symbol)

        thscode = to_thscode(req.symbol, req.exchange)
        with self._lock:
            self._subscribed[thscode] = req.vt_symbol
        self.write_log(f"Subscribed market data: {req.vt_symbol} ({thscode})")

    def send_order(self, req: OrderRequest) -> str:
        self.write_log(
            "FUYAO 是纯数据网关（行情/历史），不支持交易下单",
            level=logging.WARNING,
        )
        return ""

    def cancel_order(self, req: CancelRequest) -> None:
        self.write_log("FUYAO 是纯数据网关，不支持撤单", level=logging.WARNING)

    def query_account(self) -> None:
        return

    def query_position(self) -> None:
        return

    def _resolve_futures_thscode(
        self, symbol: str, exchange: Exchange
    ) -> str | None:
        """把 CTP 合约代码（rb2510 / IF2612 / AP611）解析为 fuyao thscode。

        优先按交易所后缀映射直连（大写），不行再经 ticker-search
        动态解析；结果（含负缓存）缓存在进程内。
        """
        key = symbol.strip().upper()
        cache_key = f"{key}.{exchange.value}"
        if cache_key in self._ths_cache:
            return self._ths_cache[cache_key]

        thscode: str | None = None
        suffix = FUTURES_SUFFIX.get(exchange)
        if suffix:
            thscode = f"{key}.{suffix}"
        else:
            thscode = self._search_futures_thscode(key)
        self._ths_cache[cache_key] = thscode
        if thscode:
            self.write_log(f"FUYAO 期货代码解析: {symbol} -> {thscode}")
        return thscode

    def _search_futures_thscode(self, key: str) -> str | None:
        """ticker-search 兜底解析；无结果时写入负缓存。"""
        try:
            data = self._request(
                TICKER_SEARCH_URL,
                params={"q": key, "asset_type": "futures", "limit": 20},
                timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            self.write_log(
                f"FUYAO 期货代码搜索失败 {key}: {e}", level=logging.WARNING
            )
            self._ths_cache[key] = None
            return None
        thscode: str | None = None
        for item in (data.get("item") or []):
            if str(item.get("ticker") or "").upper() == key:
                thscode = item.get("thscode")
                break
        if not thscode and (data.get("item") or []):
            thscode = data["item"][0].get("thscode")
        self._ths_cache[key] = thscode
        return thscode

    def _query_futures_daily(
        self, req: HistoryRequest, thscode: str,
        start_ms: int, end_ms: int,
    ) -> list[BarData]:
        """期货日K（端外仅 day_1）。"""
        data = self._request(
            FUTURES_DAILY_URL,
            params={"thscode": thscode, "start": start_ms, "end": end_ms},
            timeout=20,
        )
        bars: list[BarData] = []
        for row in (data.get("item") or []):
            ts = row.get("timestamp")
            if not ts:
                continue
            # 日K统一归一到交易日 00:00（与其它数据源同键，避免双源重复）
            dt = datetime.fromtimestamp(ts / 1000).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            bars.append(
                BarData(
                    gateway_name=self.gateway_name,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=dt,
                    interval=Interval.DAILY,
                    open_price=float(row.get("open_price") or 0),
                    close_price=float(row.get("close_price") or 0),
                    high_price=float(row.get("high_price") or 0),
                    low_price=float(row.get("low_price") or 0),
                    volume=float(row.get("volume") or 0),
                    turnover=float(row.get("turnover") or 0),
                )
            )
        return bars

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        """从同花顺 API 获取日线历史。

        A 股：/api/a-share/prices/historical（默认前复权）；
        期货：/api/futures/prices/daily（端外仅 day_1，CTP 代码
        经 ticker-search 动态解析为 thscode）。
        其它周期返回空，由上层回退到合约所属网关。
        """
        if req.interval not in (Interval.DAILY, None):
            self.write_log(
                f"FUYAO 历史仅支持日线，忽略 {req.interval}",
                level=logging.WARNING,
            )
            return []
        if req.exchange not in self.exchanges:
            return []

        start_ms = int(req.start.timestamp() * 1000)
        end_dt = req.end or datetime.now()
        end_ms = int(end_dt.timestamp() * 1000)
        if end_ms - start_ms > MAX_HISTORY_SPAN_MS:
            start_ms = end_ms - MAX_HISTORY_SPAN_MS
            self.write_log(
                "FUYAO 历史查询跨度超过10年，已自动截断起点",
                level=logging.WARNING,
            )

        # 期货分支：CTP 代码解析（后缀映射 + 搜索兜底）+ 期货日K端点
        if req.exchange in FUTURES_EXCHANGES:
            thscode = self._resolve_futures_thscode(req.symbol, req.exchange)
            if not thscode:
                return []

            def _attempt(code: str) -> list[BarData]:
                try:
                    return self._query_futures_daily(
                        req, code, start_ms, end_ms
                    )
                except Exception as e:  # noqa: BLE001
                    self.write_log(
                        f"FUYAO 期货历史查询失败 {code}: {e}",
                        level=logging.ERROR,
                    )
                    return []

            bars = _attempt(thscode)
            # 后缀直连无数据时（如映射过期），降级 ticker-search 重试一次
            if not bars and thscode.split(".")[-1] == FUTURES_SUFFIX.get(
                req.exchange
            ):
                alt = self._search_futures_thscode(
                    req.symbol.strip().upper()
                )
                if alt and alt != thscode:
                    self.write_log(
                        f"FUYAO 期货直连无数据，降级搜索重试: "
                        f"{thscode} -> {alt}"
                    )
                    bars = _attempt(alt)
            self.write_log(
                f"FUYAO 期货历史查询成功 {thscode}: {len(bars)} 根日线"
            )
            return bars

        thscode = to_thscode(req.symbol, req.exchange)

        try:
            data = self._request(
                HISTORICAL_URL,
                params={
                    "thscode": thscode,
                    "interval": "1d",
                    "start": start_ms,
                    "end": end_ms,
                    "adjust": "forward",
                },
                timeout=20,
            )
        except FuyaoApiError as e:
            self.write_log(f"FUYAO 历史查询失败 {thscode}: {e}",
                           level=logging.ERROR)
            return []
        except Exception as e:
            self.write_log(f"FUYAO 历史查询失败 {thscode}: {e}",
                           level=logging.ERROR)
            return []

        bars: list[BarData] = []
        for row in data.get("item") or []:
            date_ms = row.get("date_ms")
            if not date_ms:
                continue
            # date_ms 为交易日「次日零点」的毫秒时间戳（北京时间），
            # utcfromtimestamp 得到前一日 16:00，+8h 归一到交易日 00:00，
            # 与腾讯等其它数据源同键，save_bars upsert 直接覆盖不重复
            day = datetime.utcfromtimestamp(date_ms / 1000) + timedelta(hours=8)
            day = day.replace(hour=0, minute=0, second=0, microsecond=0)
            bars.append(
                BarData(
                    gateway_name=self.gateway_name,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=day,
                    interval=Interval.DAILY,
                    open_price=float(row.get("open_price") or 0),
                    close_price=float(row.get("close_price") or 0),
                    high_price=float(row.get("high_price") or 0),
                    low_price=float(row.get("low_price") or 0),
                    volume=float(row.get("volume") or 0),
                    turnover=float(row.get("turnover") or 0),
                )
            )
        self.write_log(
            f"FUYAO 历史查询成功 {thscode}: {len(bars)} 根日线"
            f"（前复权）"
        )
        return bars

    # ------------------------------------------------------------------
    # 快照轮询（可选，enable_quotes 开启时）
    # ------------------------------------------------------------------

    def _register_futures_contracts(self) -> None:
        """拉取 fuyao 期货合约全表并注册到 OMS（分页，幂等）。

        使无 CTP 环境下期货合约可被检索、加入回测；实时行情仍由
        CTP 承担（fuyao 期货仅提供日K历史）。
        """
        registered = 0
        offset = 0
        try:
            while True:
                data = self._request(
                    FUTURES_CONTRACTS_URL,
                    params={"limit": 1000, "offset": offset},
                    timeout=20,
                )
                items = data.get("item") or []
                for it in items:
                    exchange = EXCHANGE_BY_CODE.get(it.get("exchange_code") or "")
                    ticker = it.get("ticker")
                    if not exchange or not ticker:
                        continue
                    contract = ContractData(
                        gateway_name=self.gateway_name,
                        symbol=ticker,
                        exchange=exchange,
                        name=it.get("name") or ticker,
                        product=Product.FUTURES,
                        size=1,
                        pricetick=1.0,
                        min_volume=1,
                        history_data=True,
                    )
                    self._contracts[contract.vt_symbol] = contract
                    self.on_contract(contract)
                    registered += 1
                if len(items) < 1000:
                    break
                offset += 1000
            self.write_log(
                f"FUYAO 期货合约表注册完成: {registered} 个合约"
                "（含连续/主连/加权）"
            )
        except Exception as e:  # noqa: BLE001 - 合约表失败不阻断连接
            self.write_log(
                f"FUYAO 期货合约表拉取失败（历史查询不受影响）: {e}",
                level=logging.WARNING,
            )

    def _register_contract(
        self, symbol: str, exchange: Exchange, name: str
    ) -> None:
        product = Product.ETF if symbol.startswith(("5", "1")) else Product.EQUITY
        contract = ContractData(
            gateway_name=self.gateway_name,
            symbol=symbol,
            exchange=exchange,
            name=name,
            product=product,
            size=1,
            pricetick=0.01,
            min_volume=100,  # A 股一手
            history_data=True,
        )
        self._contracts[contract.vt_symbol] = contract
        self.on_contract(contract)

    def _run_poll(self) -> None:
        """轮询已订阅代码的行情快照并推送 Tick。"""
        while self._active:
            time.sleep(self._poll_interval)
            if not self._active:
                break

            with self._lock:
                codes = list(self._subscribed.keys())
                option_codes = list(self._option_subscribed.keys())

            try:
                if option_codes:
                    self._poll_options(option_codes)
            except FuyaoApiError as e:
                self.write_log(f"FUYAO 快照轮询失败: {e}", level=logging.WARNING)
            except Exception as e:
                self.write_log(f"FUYAO 快照轮询错误: {e}", level=logging.WARNING)

    def _poll_once(self, codes: list[str]) -> None:
        """一轮轮询：A 股走批量快照，期货逐个拉当日分时。"""
        futures_codes = [c for c in codes if not c.endswith((".SH", ".SZ", ".BJ"))]
        ashare_codes = [c for c in codes if c not in futures_codes]

        if ashare_codes and self._enable_quotes:
            self._poll_ashare_snapshot(ashare_codes)
        for code in futures_codes:
            try:
                self._poll_futures_intraday(code)
            except FuyaoApiError as e:
                self.write_log(f"FUYAO 期货分时轮询失败 {code}: {e}",
                               level=logging.WARNING)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"FUYAO 期货分时轮询错误 {code}: {e}",
                               level=logging.WARNING)

    def _poll_ashare_snapshot(self, codes: list[str]) -> None:
        """一次批量快照请求（thscodes 逗号分隔）。"""
        data = self._request(
            SNAPSHOT_URL, params={"thscodes": ",".join(codes)}, timeout=10
        )
        ts = data.get("timestamp")
        if ts and ts == self._last_snapshot_ts:
            return  # 快照未更新（休市）
        self._last_snapshot_ts = ts

        for item in data.get("item") or []:
            thscode = item.get("thscode", "")
            vt_symbol = self._subscribed.get(thscode)
            if not vt_symbol:
                continue
            contract = self._contracts.get(vt_symbol)
            if not contract:
                continue

            tick = self._parse_snapshot(item, contract)
            if tick is not None:
                self.on_tick(tick)

    def _is_option_symbol(self, symbol: str, exchange: Exchange) -> bool:
        """是否期权合约：解析出的 thscode 在期权目录中即为期权
        （resolve 内部已处理 CTP/横线/大小写变体；期货代码不在目录）。"""
        try:
            resolved = self._resolve_option_thscode(symbol, exchange.value)
            if not resolved:
                return False
            from sqlalchemy import select

            from ....db.models import OptionContractModel
            from ....db.session import create_session

            session = create_session()
            try:
                row = session.execute(
                    select(OptionContractModel.id).where(
                        OptionContractModel.thscode == resolved
                    )
                ).first()
            finally:
                session.close()
            return row is not None
        except Exception:  # noqa: BLE001
            return False

    def _resolve_option_thscode(self, symbol: str, exchange_code: str) -> str | None:
        """CTP/DCE 风格期权代码 → fuyao thscode（含横线/大小写变体）。"""
        from ....services.option_sync import resolve_option_thscode

        return resolve_option_thscode(symbol, exchange_code)

    def _poll_options(self, thscodes: list[str]) -> None:
        """期权当日分时轮询：合成分钟级 Tick（真实行情，延迟约 1 分钟）。"""
        for thscode in thscodes:
            try:
                self._poll_option_intraday(thscode)
            except FuyaoApiError as e:
                # 休市时段 5003 属正常，降噪为 debug 级不刷屏
                self.write_log(
                    f"FUYAO 期权分时轮询 {thscode}: {e}",
                    level=logging.DEBUG,
                )
            except Exception as e:  # noqa: BLE001
                self.write_log(
                    f"FUYAO 期权分时轮询错误 {thscode}: {e}",
                    level=logging.WARNING,
                )

    def _poll_option_intraday(self, thscode: str) -> None:
        vt_symbol = self._option_subscribed.get(thscode)
        if not vt_symbol:
            return

        data = self._request(
            OPTIONS_INTRADAY_URL,
            params={"thscode": thscode, "session": "intraday"},
            timeout=10,
        )
        items = [
            i for i in (data.get("item") or [])
            if i.get("price") is not None and i.get("timestamp")
        ]
        if not items:
            return

        last_ts = items[-1].get("timestamp")
        if self._last_option_ts.get(thscode) == last_ts:
            return
        self._last_option_ts[thscode] = last_ts

        prices = [i["price"] for i in items]
        last = items[-1]
        self.on_tick(
            TickData(
                gateway_name=self.gateway_name,
                symbol=vt_symbol.rsplit(".", 1)[0],
                name="",
                exchange=self._exchange_for_vt(vt_symbol),
                datetime=datetime.fromtimestamp(last["timestamp"] / 1000),
                last_price=float(last["price"]),
                open_price=float(prices[0]),
                high_price=float(max(prices)),
                low_price=float(min(prices)),
                volume=float(last.get("volume") or 0),
                turnover=float(last.get("turnover") or 0),
            )
        )

    def _exchange_for_vt(self, vt_symbol: str) -> Exchange:
        try:
            _, exchange = extract_vt_symbol(vt_symbol)
            return exchange
        except Exception:  # noqa: BLE001
            return Exchange.LOCAL

    def _poll_futures_intraday(self, thscode: str) -> None:
        """期货当日分时轮询：合成分钟级 Tick（volume 为当日累计）。"""
        vt_symbol = self._subscribed.get(thscode)
        if not vt_symbol:
            return

        data = self._request(
            FUTURES_INTRADAY_URL,
            params={"thscode": thscode, "session": "intraday"},
            timeout=10,
        )
        items = [
            i for i in (data.get("item") or [])
            if i.get("price") is not None and i.get("timestamp")
        ]
        if not items:
            return

        # 与上一轮相同的最新时间戳（休市/无新分时）则跳过
        last_ts = items[-1].get("timestamp")
        if self._last_futures_ts.get(thscode) == last_ts:
            return
        self._last_futures_ts[thscode] = last_ts

        prices = [i["price"] for i in items]
        last = items[-1]
        contract = next(
            (c for k, c in self._contracts.items()
             if k.upper() == vt_symbol.upper()),
            None,
        )
        # symbol 跟随订阅方原始大小写（CTP 风格 rb2701），保证
        # 前端按 vt_symbol 匹配时一致
        self.on_tick(
            TickData(
                gateway_name=self.gateway_name,
                symbol=vt_symbol.rsplit(".", 1)[0],
                name=contract.name if contract else "",
                exchange=contract.exchange,
                datetime=datetime.fromtimestamp(last["timestamp"] / 1000),
                last_price=float(last["price"]),
                open_price=float(prices[0]),
                high_price=float(max(prices)),
                low_price=float(min(prices)),
                volume=float(last.get("volume") or 0),   # 当日累计
                turnover=float(last.get("turnover") or 0),
            )
        )

    def _parse_snapshot(
        self, item: dict, contract: ContractData
    ) -> TickData | None:
        """把快照 item 解析为 TickData（无五档盘口）。"""
        last = float(item.get("last_price") or 0)
        if last <= 0:
            return None

        ts = item.get("timestamp") or int(time.time() * 1000)
        # 首次见到时用 ticker 更新合约名称（快照不含中文名）
        if contract.name == contract.symbol and item.get("ticker"):
            self._register_contract(
                contract.symbol, contract.exchange, item["ticker"]
            )

        return TickData(
            gateway_name=self.gateway_name,
            symbol=contract.symbol,
            exchange=contract.exchange,
            datetime=datetime.fromtimestamp(ts / 1000),
            name=contract.name,
            last_price=last,
            pre_close=float(item.get("prev_price") or 0),
            open_price=float(item.get("open_price") or 0),
            high_price=float(item.get("high_price") or 0),
            low_price=float(item.get("low_price") or 0),
            volume=float(item.get("volume") or 0),    # 已是股
            turnover=float(item.get("turnover") or 0),
        )
