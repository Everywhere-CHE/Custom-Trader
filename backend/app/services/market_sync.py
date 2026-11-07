"""全市场 A 股日线数据底座：批量同步 + 盘后增量。

为研究类功能（因子/全市场扫描/情绪周期等）提供全市场截面数据：

- 标的全量清单：fuyao ``/api/meta/tickers/list``（offset 翻页，~5400 只）
- 历史行情：fuyao ``/api/a-share/prices/historical``（前复权日线，
  date_ms 为交易日次日零点语义，与既有 bars 表同键，upsert 幂等）
- 限流：默认 5 req/s 自适应——遇 4001 限流退避并降速，恢复后缓升
- 断点续传：全量任务每 50 只 checkpoint，失败重跑自动跳过已完成
- 盘后增量：每个交易自然日 15:10 后自动跑一次（幂等，节假日浪费一次
  请求量但无害）；全量只手动触发

状态持久化 ``data/market_sync_state.json``。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from ..config import DATA_DIR
from ..core.constant import Exchange, Interval
from ..core.object import BarData
from ..core.utility import CHINA_TZ
from ..fuyao_config import load_fuyao_config

STATE_FILE = DATA_DIR / "market_sync_state.json"
SYMBOLS_FILE = DATA_DIR / "market_symbols.json"

BASE_URL = "https://fuyao.aicubes.cn"
TICKERS_URL = f"{BASE_URL}/api/meta/tickers/list"
HISTORICAL_URL = f"{BASE_URL}/api/a-share/prices/historical"

EXCHANGE_MAP = {
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
    "BJ": Exchange.BSE,
}

# 限流参数
DEFAULT_RATE = 5.0        # req/s
MIN_RATE = 1.5
BACKOFF_ON_429 = 4.0
MAX_RETRIES = 5

# 全量任务默认历史天数
DEFAULT_FULL_DAYS = 800


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


class MarketSyncManager:
    """单飞的全市场同步管理器（后台线程 + 自适应限流）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "status": "idle",
            "mode": "",
            "progress": "",
            "total_symbols": 0,
            "done_symbols": 0,
            "saved_bars": 0,
            "errors": 0,
            "started_at": None,
            "finished_at": None,
            "error": "",
        }
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._persisted = _load_state()
        self._scheduler_started = False

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

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
            "last_full_sync": persisted.get("last_full_sync"),
            "last_incremental": persisted.get("last_incremental"),
            "symbol_count": persisted.get("symbol_count"),
        }

    def request_cancel(self) -> bool:
        if not self.running:
            return False
        self._cancel.set()
        return True

    def _update(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

    # ------------------------------------------------------------------
    # HTTP：自适应限流 + 4001 退避
    # ------------------------------------------------------------------

    def _make_client(self, api_key: str) -> httpx.Client:
        return httpx.Client(
            base_url=BASE_URL,
            headers={"X-api-key": api_key},
            timeout=25,
        )

    def _get_item(
        self,
        client: httpx.Client,
        url: str,
        params: dict,
        rate_box: dict,
    ) -> dict:
        """带限流/退避的 GET，返回信封 data。失败抛 RuntimeError。"""
        retries = 0
        while True:
            # 令牌间隔
            interval = 1.0 / rate_box["rate"]
            wait = rate_box["next_time"] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            rate_box["next_time"] = time.monotonic() + interval

            resp = client.get(url, params=params)
            try:
                envelope = resp.json()
            except ValueError:
                raise RuntimeError(f"非 JSON 响应 HTTP {resp.status_code}") from None
            code = envelope.get("code")
            if code == 0 or code == 200:
                data = envelope.get("data") or {}
                # 缓慢恢复限速
                rate_box["rate"] = min(DEFAULT_RATE, rate_box["rate"] * 1.05)
                return data
            if code == 4001 or resp.status_code == 429:
                retries += 1
                if retries > MAX_RETRIES:
                    raise RuntimeError(f"限流重试耗尽（{MAX_RETRIES} 次）")
                rate_box["rate"] = max(MIN_RATE, rate_box["rate"] * 0.5)
                time.sleep(BACKOFF_ON_429 * retries)
                continue
            raise RuntimeError(
                f"fuyao code={code}: {str(envelope.get('message'))[:80]}"
            )

    # ------------------------------------------------------------------
    # 标的清单
    # ------------------------------------------------------------------

    def fetch_all_symbols(self, client: httpx.Client, rate_box: dict) -> list[dict]:
        """全量 A 股标的清单（offset 翻页）。"""
        symbols: list[dict] = []
        offset = 0
        while True:
            data = self._get_item(
                client,
                TICKERS_URL,
                {"asset_type": "a-share", "limit": 1000, "offset": offset},
                rate_box,
            )
            items = data.get("item") or []
            if not items:
                break
            for it in items:
                suffix = it.get("exchange") or ""
                ticker = it.get("ticker") or ""
                if it.get("end_date"):
                    continue  # 已退市
                exchange = EXCHANGE_MAP.get(suffix)
                if not exchange or len(ticker) != 6 or not ticker.isdigit():
                    continue
                symbols.append(
                    {
                        "symbol": ticker,
                        "exchange": exchange,
                        "thscode": it["thscode"],
                        "name": it.get("name") or "",
                    }
                )
            offset += len(items)
            if len(items) < 1000:
                break
        return symbols

    # ------------------------------------------------------------------
    # 同步执行
    # ------------------------------------------------------------------

    def _run_sync(self, mode: str, days: int) -> None:
        class _S:  # fuyao_config 只读默认时需要一个占位对象
            fuyao_api_key = ""
            fuyao_poll_interval = 5.0
            fuyao_enable_quotes = False

        api_key = str(load_fuyao_config(_S()).get("api_key") or "")
        if not api_key:
            self._update(status="failed", error="未配置 FUYAO API Key")
            return

        rate_box = {"rate": DEFAULT_RATE, "next_time": 0.0}
        client = self._make_client(api_key)
        data_engine = None

        try:
            from ..db.data_engine import DataEngine

            class _StubEvent:
                def register(self, *a, **k):  # noqa: ANN002
                    pass

                def unregister(self, *a, **k):  # noqa: ANN002
                    pass

            class _ME:  # 只用 get_engine('data')；事件注册到桩上，独立于主引擎
                def get_engine(self, name):  # noqa: ANN001
                    if not hasattr(self, "_de"):
                        self._de = DataEngine(_ME(), _StubEvent())
                    return self._de if name == "data" else None

            data_engine = _ME().get_engine("data")

            self._update(
                status="running", mode=mode, progress="拉取标的清单…",
                total_symbols=0, done_symbols=0, saved_bars=0, errors=0,
                started_at=datetime.now(CHINA_TZ).isoformat(),
                finished_at=None, error="",
            )
            self._cancel.clear()

            symbols = self.fetch_all_symbols(client, rate_box)
            self._update(total_symbols=len(symbols))
            if not symbols:
                self._update(status="failed", error="标的清单为空")
                return

            # 标的名单落盘（含名称，供涨停判定 ST 识别 / 前端展示）
            try:
                SYMBOLS_FILE.write_text(
                    json.dumps(
                        [
                            {
                                "symbol": s["symbol"],
                                "exchange": s["exchange"].value,
                                "name": s["name"],
                                "thscode": s["thscode"],
                            }
                            for s in symbols
                        ],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass

            end_ms = int(time.time() * 1000)
            start_ms = end_ms - int(days) * 86400 * 1000

            done = 0
            saved_total = 0
            errors = 0
            checkpoint = self._load_checkpoint(mode)

            for sym in symbols:
                if self._cancel.is_set():
                    self._update(
                        status="cancelled", progress="已取消",
                        finished_at=datetime.now(CHINA_TZ).isoformat(),
                    )
                    return
                done += 1
                key = f"{sym['symbol']}.{sym['exchange'].value}"
                if checkpoint and key in checkpoint:
                    continue  # 断点续传：跳过已完成
                try:
                    data = self._get_item(
                        client,
                        HISTORICAL_URL,
                        {
                            "thscode": sym["thscode"],
                            "interval": "1d",
                            "start": start_ms,
                            "end": end_ms,
                            "adjust": "forward",
                        },
                        rate_box,
                    )
                    bars = self._parse_bars(sym, data)
                    if bars:
                        data_engine.save_bars(bars)
                        saved_total += len(bars)
                except Exception:  # noqa: BLE001 —— 单标的失败计数不中断
                    errors += 1

                self._update(
                    done_symbols=done,
                    saved_bars=saved_total,
                    errors=errors,
                    progress=f"同步中 {done}/{len(symbols)}（{sym['name'] or key}）",
                )
                # 每 50 只 checkpoint（按顺序遍历，保存已完成键集合供续传）
                if done % 50 == 0:
                    self._save_checkpoint(mode, key, done)

            now = datetime.now(CHINA_TZ).isoformat()
            self._update(
                status="done",
                progress=f"完成：{done} 标的 / {saved_total} 根K线 / {errors} 错误",
                finished_at=now,
            )
            self._persisted.update(
                {
                    "last_full_sync" if mode == "full" else "last_incremental": now,
                    "symbol_count": len(symbols),
                    "last_mode": mode,
                }
            )
            _save_state(self._persisted)
            self._clear_checkpoint(mode)
        except Exception as exc:  # noqa: BLE001
            self._update(
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(CHINA_TZ).isoformat(),
            )
        finally:
            client.close()

    def _parse_bars(self, sym: dict, data: dict) -> list[BarData]:
        bars: list[BarData] = []
        for row in data.get("item") or []:
            date_ms = row.get("date_ms")
            if not date_ms:
                continue
            # date_ms 为交易日次日零点（北京时间），归一到交易日 00:00
            day = datetime.utcfromtimestamp(date_ms / 1000) + timedelta(hours=8)
            day = day.replace(hour=0, minute=0, second=0, microsecond=0)
            bars.append(
                BarData(
                    gateway_name="FUYAO",
                    symbol=sym["symbol"],
                    exchange=sym["exchange"],
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
        return bars

    # ------------------------------------------------------------------
    # 断点（记录已完成游标，重跑时跳过）
    # ------------------------------------------------------------------

    def _checkpoint_file(self, mode: str):
        return DATA_DIR / f"market_sync_ckpt_{mode}.json"

    def _load_checkpoint(self, mode: str) -> set[str]:
        try:
            f = self._checkpoint_file(mode)
            if f.is_file():
                return set(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
        return set()

    def _save_checkpoint(self, mode: str, last_key: str, done: int) -> None:
        try:
            f = self._checkpoint_file(mode)
            data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
            keys = data.get("keys") or []
            keys.append(last_key)
            f.write_text(
                json.dumps({"keys": keys, "done": done}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    def _clear_checkpoint(self, mode: str) -> None:
        try:
            self._checkpoint_file(mode).unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 对外：启动任务
    # ------------------------------------------------------------------

    def start(self, mode: str, days: int) -> dict:
        with self._lock:
            if self._state["status"] == "running":
                return {"ok": False, "message": "已有同步任务在运行"}
            if mode not in ("full", "incremental"):
                return {"ok": False, "message": "mode 必须为 full 或 incremental"}
            self._state.update(
                status="pending", mode=mode, progress="排队中",
                total_symbols=0, done_symbols=0, saved_bars=0, errors=0,
                error="",
            )
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run_sync,
                args=(mode, max(30, min(int(days), 3000))),
                name=f"MarketSync-{mode}",
                daemon=True,
            )
            self._thread.start()
            return {"ok": True, "message": f"已启动{('全量' if mode == 'full' else '增量')}同步"}

    # ------------------------------------------------------------------
    # 盘后增量调度器
    # ------------------------------------------------------------------

    def start_scheduler(self) -> None:
        """每 10 分钟检查一次：交易日下午 15:10 后做当日增量（单飞幂等）。"""
        if self._scheduler_started:
            return

        def _loop() -> None:
            while True:
                try:
                    self._scheduler_tick()
                except Exception:  # noqa: BLE001 —— 调度异常静默
                    pass
                time.sleep(600)

        thread = threading.Thread(target=_loop, name="MarketSyncScheduler", daemon=True)
        thread.start()
        self._scheduler_started = True

    def _scheduler_tick(self) -> None:
        now = datetime.now(CHINA_TZ)
        if now.weekday() >= 5:
            return  # 周末
        if now.hour < 15 or (now.hour == 15 and now.minute < 10):
            return  # 15:10 前（收盘数据未生成）
        if now.hour >= 23:
            return
        last = str(self._persisted.get("last_incremental") or "")[:10]
        if last == now.strftime("%Y-%m-%d"):
            return  # 今日已做
        if self.running:
            return
        self.start("incremental", days=12)


# 模块级单例
market_sync_manager = MarketSyncManager()
