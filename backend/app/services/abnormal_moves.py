"""异动监控：交易所《交易规则》异常波动口径的偏离预警。

口径（交易所异常波动标准，可验证部分）：

- **3 个交易日收盘价涨跌幅偏离值累计**：主板 ±20%、创业板/科创板 ±30%、
  北交所 ±40%
- 偏离基准：本平台无行业指数数据，使用**全市场等权平均 N 日涨幅**作为
  基准（口径 = 个股超额偏离，诚实标注，不冒充交易所精确口径）
- **接近度** closeness = 偏离 / 该方向阈值：≥1.0 触发、≥0.7 边缘、
  ≥0.5 关注——即"距离触发交易所口径还差多少"
- 10 日 / 30 日累计涨幅作为参考列输出（不做阈值判定）

全部从本地全市场日 K 派生（复用 market_phase 的数据加载与标的名单），
结果缓存 30 分钟。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from ..core.utility import CHINA_TZ
from .market_phase import (
    _load_daily_bars,
    load_symbol_names,
)

# 3 日异常波动口径（按代码前缀匹配，未匹配用主板）
_BOARD_RULES = (
    (("300", "301", "688", "689"), "创/科", 0.30),
    (("83", "87", "88", "92", "43"), "北交所", 0.40),
)
_DEFAULT_BOARD = ("主板", 0.20)

_LEVELS = ((1.0, "触发"), (0.7, "边缘"), (0.5, "关注"))

_cache_lock = threading.Lock()
_cache: dict[str, tuple[str, dict]] = {}


def _board_of(symbol: str) -> tuple[str, float]:
    for prefixes, board, threshold in _BOARD_RULES:
        if symbol.startswith(prefixes):
            return board, threshold
    return _DEFAULT_BOARD


def _ret_over_closes(close_now: float, close_prev: float) -> float | None:
    if not close_prev:
        return None
    return close_now / close_prev - 1


def compute_abnormal(
    lookback_days: int = 60,
    max_items: int = 40,
    force: bool = False,
) -> dict:
    """计算全市场 3 日偏离预警 + 10/30 日参考列。"""
    now = datetime.now(CHINA_TZ)
    if not force:
        with _cache_lock:
            for _as_of, (at, cached) in list(_cache.items()):
                try:
                    age = (now - datetime.fromisoformat(at)).total_seconds() / 60
                except ValueError:
                    age = 1e9
                if age <= 30:
                    return cached
                _cache.pop(_as_of, None)

    series = _load_daily_bars(lookback_days)
    if len(series) < 100:
        return {
            "status": "insufficient",
            "universe_size": len(series),
            "hint": "全市场日线数据不足（需 ≥100 只，先在设置页跑全市场同步）",
        }
    names = load_symbol_names()

    # 各标的按日期索引收盘价 + 计算区间涨幅
    items: list[dict] = []
    market_ret3_values: list[float] = []

    precomputed: list[dict] = []
    for key, bars in series.items():
        if len(bars) < 2:
            continue
        symbol = key.rsplit(".", 1)[0]
        board, threshold = _board_of(symbol)
        closes = [float(b.close_price or 0) for b in bars]
        dates = [b.datetime.date().isoformat() for b in bars]
        if not closes[-1]:
            continue

        def ret(n: int) -> float | None:
            idx = len(closes) - 1 - n
            if idx < 0 or not closes[idx]:
                return None
            return closes[-1] / closes[idx] - 1

        ret3 = ret(3)
        ret10 = ret(10)
        ret30 = ret(30)
        if ret3 is None:
            continue
        market_ret3_values.append(ret3)
        precomputed.append(
            {
                "vt_symbol": key,
                "symbol": symbol,
                "name": names.get(key, symbol),
                "board": board,
                "threshold": threshold,
                "ret3": ret3,
                "ret10": ret10,
                "ret30": ret30,
                "close": closes[-1],
                "date": dates[-1],
            }
        )

    if len(market_ret3_values) < 100:
        return {
            "status": "insufficient",
            "hint": "有效样本不足",
        }
    baseline3 = sum(market_ret3_values) / len(market_ret3_values)

    for row in precomputed:
        deviation = row["ret3"] - baseline3
        row["dev3"] = round(deviation, 4)
        # 方向感知阈值：正向用 +阈值，负向用 −阈值（绝对值同为 threshold）
        if deviation >= 0:
            closeness = deviation / row["threshold"]
        else:
            closeness = deviation / (-row["threshold"])
        row["closeness"] = round(closeness, 3)
        level = "-"
        for trigger, name in _LEVELS:
            if abs(closeness) >= trigger:
                level = (
                    f"{name}·涨" if deviation >= 0 else f"{name}·跌"
                )
                break
        row["level"] = level
        items.append(row)

    items.sort(key=lambda x: -abs(x["closeness"]))
    top = [
        {
            "vt_symbol": it["vt_symbol"],
            "name": it["name"],
            "board": it["board"],
            "close": it["close"],
            "ret3_pct": round(it["ret3"] * 100, 2),
            "dev3_pct": round(it["dev3"] * 100, 2),
            "threshold_pct": round(it["threshold"] * 100),
            "closeness": it["closeness"],
            "level": it["level"],
            "ret10_pct": round(it["ret10"] * 100, 2) if it["ret10"] is not None else None,
            "ret30_pct": round(it["ret30"] * 100, 2) if it["ret30"] is not None else None,
            "date": it["date"],
        }
        for it in items[:max_items]
    ]

    counts = {"触发": 0, "边缘": 0, "关注": 0}
    for it in items:
        for trigger, name in _LEVELS:
            if abs(it["closeness"]) >= trigger:
                counts[name] += 1
                break

    result = {
        "status": "ok",
        "as_of": items[0]["date"] if items else None,
        "universe_size": len(series),
        "baseline3_pct": round(baseline3 * 100, 2),
        "counts": counts,
        "items": top,
        "note": (
            "口径：3 个交易日收盘涨幅偏离 = 个股3日涨幅 − 全市场等权平均3日涨幅；"
            "阈值按交易所异常波动标准（主板±20%/创科±30%/北交所±40%）；"
            "接近度 = 偏离/阈值（≥1.0 触发 / ≥0.7 边缘 / ≥0.5 关注）。"
            "10/30日涨幅仅作参考列，不做口径判定"
        ),
    }
    with _cache_lock:
        _cache["latest"] = (now.isoformat(), result)
    return result
