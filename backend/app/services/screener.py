"""全市场策略扫描器：内置信号策略 × 全市场日 K（NumPy 向量化）。

定位（借鉴 TSP 的"策略协议"而非其 Polars 性能层）：

- 6 个内置信号策略（可解释、参数集中在常量区）：
  1. ``breakout_high_20``  收盘突破 20 日新高
  2. ``volume_surge``      放量上涨（量比 ≥2 且 3 日涨幅 >3%）
  3. ``ma_bull``           均线多头排列（收盘>MA5>MA10>MA20>MA60）
  4. ``golden_cross``      MA5 金叉 MA10
  5. ``limit_up_gene``     涨停基因（近 60 交易日涨停 ≥2 次）
  6. ``rps_60``            RPS60 相对强度 ≥90（60 日涨幅全市场百分位）
- 每次扫描拉取最近 160 自然日全市场日 K（~39 万行，1-3 秒），
  NumPy 逐标的向量化判定，总耗时约 5 秒；结果缓存 30 分钟
- 停牌/无数据/新中亚标的 fail-closed 跳过（窗口不足不硬算）
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import numpy as np

from ..core.utility import CHINA_TZ
from .market_phase import _limit_ratio, _load_daily_bars, load_symbol_names

_CACHE_TTL_MINUTES = 30
_LOOKBACK_DAYS = 160          # 自然日（≈110 交易日，覆盖 MA60 与 RPS60）

_cache_lock = threading.Lock()
_cache: dict[str, tuple[str, dict]] = {}  # key -> (computed_at, result)


# ----------------------------------------------------------------------
# 策略注册表
# ----------------------------------------------------------------------

STRATEGIES: list[dict[str, str]] = [
    {
        "id": "breakout_high_20",
        "name": "突破20日新高",
        "description": "收盘价创最近 20 个交易日新高（不含当日）且当日收涨",
    },
    {
        "id": "volume_surge",
        "name": "放量上涨",
        "description": "成交量 ≥ 5日均量的 2 倍，且 3 日累计涨幅 > 3%，当日收阳",
    },
    {
        "id": "ma_bull",
        "name": "均线多头排列",
        "description": "收盘 > MA5 > MA10 > MA20 > MA60，多头趋势完整",
    },
    {
        "id": "golden_cross",
        "name": "MA5金叉MA10",
        "description": "今日 MA5 上穿 MA10（昨日 MA5 ≤ MA10，今日 MA5 > MA10）",
    },
    {
        "id": "limit_up_gene",
        "name": "涨停基因",
        "description": "近 60 个交易日内涨停（板块口径四舍五入涨停价）≥ 2 次",
    },
    {
        "id": "rps_60",
        "name": "RPS60≥90",
        "description": "60 交易日涨幅的全市场百分位 ≥ 90%（欧奈尔相对强度口径）",
    },
]

STRATEGY_IDS = tuple(s["id"] for s in STRATEGIES)


def strategy_catalog() -> list[dict]:
    return STRATEGIES


# ----------------------------------------------------------------------
# 单标的信号判定
# ----------------------------------------------------------------------

def _sma(a: np.ndarray, n: int) -> float:
    return float(a[-n:].mean()) if len(a) >= n else float("nan")


def _evaluate_symbol(
    strategy_id: str,
    key: str,
    closes: np.ndarray,
    highs: np.ndarray,
    opens: np.ndarray,
    volumes: np.ndarray,
    symbol: str,
    name: str,
    rps_60: float | None,
) -> dict | None:
    """返回信号详情 dict（命中）或 None（未命中/窗口不足）。"""
    n = len(closes)
    if n < 21:
        return None

    if strategy_id == "breakout_high_20":
        prev_high = float(highs[-21:-1].max())
        close = float(closes[-1])
        prev_close = float(closes[-2])
        if close > prev_high and close > prev_close:
            return {
                "detail": f"突破20日高点 {prev_high:.2f}",
                "extra": {"close": close, "prev_high": prev_high},
            }
        return None

    if strategy_id == "volume_surge":
        if n < 8:
            return None
        vol_ratio = float(volumes[-1]) / max(float(volumes[-6:-1].mean()), 1e-9)
        ret3 = float(closes[-1]) / float(closes[-4]) - 1
        if vol_ratio >= 2.0 and ret3 > 0.03 and closes[-1] > opens[-1]:
            return {
                "detail": f"量比 {vol_ratio:.1f}，3日 {ret3*100:+.1f}%",
                "extra": {"vol_ratio": round(vol_ratio, 2), "ret3": round(ret3 * 100, 2)},
            }
        return None

    if strategy_id == "ma_bull":
        if n < 61:
            return None
        ma5, ma10 = _sma(closes, 5), _sma(closes, 10)
        ma20, ma60 = _sma(closes, 20), _sma(closes, 60)
        close = float(closes[-1])
        if close > ma5 > ma10 > ma20 > ma60:
            return {
                "detail": f"MA60↑ {ma60:.2f}，现价 {close:.2f}",
                "extra": {"ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2)},
            }
        return None

    if strategy_id == "golden_cross":
        if n < 11:
            return None
        ma5_now, ma10_now = _sma(closes, 5), _sma(closes, 10)
        ma5_prev = float(closes[-6:-1].mean())
        ma10_prev = float(closes[-11:-1].mean())
        if ma5_prev <= ma10_prev and ma5_now > ma10_now:
            return {
                "detail": f"MA5 {ma5_now:.2f} 上穿 MA10 {ma10_now:.2f}",
                "extra": {"ma5": round(ma5_now, 2), "ma10": round(ma10_now, 2)},
            }
        return None

    if strategy_id == "limit_up_gene":
        count = 0
        look = min(n - 1, 60)
        for i in range(n - look, n):
            prev_close = float(closes[i - 1])
            if not prev_close:
                continue
            limit_price = round(prev_close * (1 + _limit_ratio(symbol, name)), 2)
            if abs(closes[i] - limit_price) < 0.001:
                count += 1
        if count >= 2:
            return {
                "detail": f"近60日涨停 {count} 次",
                "extra": {"limit_up_count": count},
            }
        return None

    if strategy_id == "rps_60":
        if rps_60 is None or n < 61:
            return None
        if rps_60 >= 90.0:
            ret60 = float(closes[-1]) / float(closes[-61]) - 1
            return {
                "detail": f"RPS60 {rps_60:.0f}（60日 {ret60*100:+.1f}%）",
                "extra": {"rps60": round(rps_60, 1), "ret60_pct": round(ret60 * 100, 1)},
            }
        return None

    return None


# ----------------------------------------------------------------------
# 全市场扫描
# ----------------------------------------------------------------------

def run_scan(strategy_ids: list[str], force: bool = False) -> dict:
    """执行全市场扫描。strategy_ids 为空 = 全部策略。"""
    ids = [s for s in strategy_ids if s in STRATEGY_IDS] or list(STRATEGY_IDS)
    cache_key = ",".join(sorted(ids))

    now = datetime.now(CHINA_TZ)
    if not force:
        with _cache_lock:
            for k, (at, cached) in list(_cache.items()):
                try:
                    age = (now - datetime.fromisoformat(at)).total_seconds() / 60
                except ValueError:
                    age = 1e9
                if age <= _CACHE_TTL_MINUTES and k == cache_key:
                    return cached
                if age > _CACHE_TTL_MINUTES:
                    _cache.pop(k, None)

    series = _load_daily_bars(_LOOKBACK_DAYS)
    if len(series) < 100:
        return {
            "status": "insufficient",
            "universe_size": len(series),
            "hint": "全市场日线数据不足（需 ≥100 只，先在设置页跑全市场同步）",
        }
    names = load_symbol_names()

    # RPS60：60 交易日涨幅的全市场百分位（一次算好共享）
    ret60_map: dict[str, float] = {}
    for key, bars in series.items():
        closes = [float(b.close_price or 0) for b in bars]
        if len(closes) >= 61 and closes[-61] and closes[-1]:
            ret60_map[key] = closes[-1] / closes[-61] - 1
    ordered = sorted(ret60_map.values())
    total = len(ordered)
    rps_map: dict[str, float] = {}
    for key, value in ret60_map.items():
        # 低于该值的样本占比 → 百分位 0-100
        lo, hi = 0, total
        arr = ordered
        while lo < hi:
            mid = (lo + hi) // 2
            if arr[mid] < value:
                lo = mid + 1
            else:
                hi = mid
        rps_map[key] = round(lo / max(total - 1, 1) * 100, 1)

    # 逐标的扫描
    results: dict[str, list[dict]] = {sid: [] for sid in ids}
    scanned = 0
    as_of = None
    for key, bars in series.items():
        scanned += 1
        closes = np.array([float(b.close_price or 0) for b in bars])
        highs = np.array([float(b.high_price or 0) for b in bars])
        opens = np.array([float(b.open_price or 0) for b in bars])
        volumes = np.array([float(b.volume or 0) for b in bars])
        if not closes[-1]:
            continue
        as_of = bars[-1].datetime.date().isoformat()
        symbol = key.rsplit(".", 1)[0]
        name = names.get(key, symbol)
        rps = rps_map.get(key)
        for sid in ids:
            hit = _evaluate_symbol(
                sid, key, closes, highs, opens, volumes, symbol, name, rps
            )
            if hit:
                results[sid].append(
                    {
                        "vt_symbol": key,
                        "name": name,
                        "close": closes[-1],
                        **hit,
                    }
                )

    # 按代码排序保证稳定输出
    for sid in results:
        results[sid].sort(key=lambda x: x["vt_symbol"])

    strategy_results = []
    for meta in STRATEGIES:
        sid = meta["id"]
        if sid in results:
            strategy_results.append(
                {
                    "id": sid,
                    "name": meta["name"],
                    "description": meta["description"],
                    "count": len(results[sid]),
                    "items": results[sid][:60],
                }
            )

    result = {
        "status": "ok",
        "as_of": as_of,
        "universe_size": len(series),
        "strategies": strategy_results,
        "elapsed_note": "全市场 NumPy 扫描，结果缓存 30 分钟",
    }
    with _cache_lock:
        _cache[cache_key] = (now.isoformat(), result)
    return result


def get_scan_cached(strategy_ids: list[str], force: bool = False) -> dict:
    return run_scan(strategy_ids, force=force)
