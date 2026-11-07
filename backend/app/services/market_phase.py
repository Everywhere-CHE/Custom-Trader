"""市场情绪：涨停连板梯队 + 情绪周期 6 阶段（冰点/启动/主升/高潮/退潮/修复）。

口径源自游资短线体系的情绪周期理论，全部派生量从本地全市场日 K 计算：

- 涨停判定：收盘价 ≥ round(昨收 × (1+板块涨幅), 2)——主板 10%、
  创业板/科创板 20%、北交所 30%、ST 主板 5%（名称含 ST）
- 梯队派生量：最高连板高度、首板宽度、2/3/5 板家数、晋级率
  （昨日涨停池今日续板比例，池 <10 家记 null）、炸板率
  （触及涨停价但收盘未封住的比例）
- 6 阶段判定：滚动分位数（p10/p40/p60/p85）自适应阈值 +
  优先级（高潮>主升>冰点>退潮>启动>修复）+ 连续 2 日同标签才切换
- 全部数据 fail-closed：窗口内数据不足时如实返回 insufficient

结果按「数据末日」缓存（同一天多次请求直接命中，无重复回算）。
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from ..config import DATA_DIR
from ..core.constant import Interval
from ..core.utility import CHINA_TZ

PHASES = ("冰点", "启动", "主升", "高潮", "退潮", "修复")

# 情绪指数分区（0-100，同花顺式沸点→冰点色带）
SENTIMENT_ZONES = (
    ("沸点", 90, 100),
    ("过热", 75, 90),
    ("微热", 60, 75),
    ("微冷", 40, 60),
    ("过冷", 25, 40),
    ("冰点", 0, 25),
)

# 情绪指数分量：(stats 字段, 权重, 反向)。None 值取中性 50。
_SENTIMENT_COMPONENTS = (
    ("limit_up", 0.30, False),       # 涨停家数
    ("max_streak", 0.20, False),     # 最高连板高度
    ("promotion_rate", 0.20, False), # 晋级率
    ("blast_rate", 0.15, True),      # 炸板率（越高越冷）
    ("ge2", 0.10, False),            # 2板+宽度
    ("limit_down", 0.05, True),      # 跌停家数（越多越冷）
)


def _sentiment_zone(score: float) -> str:
    for name, low, high in SENTIMENT_ZONES:
        if low <= score <= high:
            return name
    return "微冷"


def _compute_sentiment(stats: list[dict]) -> list[dict]:
    """情绪指数（0-100）：各分量按窗口内百分位归一后加权。

    百分位归一自校准，不依赖硬编码阈值；None 分量取中性 50。
    """
    # 预排序各分量取值用于百分位查找
    sorted_cols: dict[str, list[float]] = {}
    for field, _w, _inv in _SENTIMENT_COMPONENTS:
        vals = sorted(
            float(s[field]) for s in stats if s.get(field) is not None
        )
        sorted_cols[field] = vals

    def pct_rank(field: str, value: float) -> float:
        vals = sorted_cols[field]
        if not vals:
            return 50.0
        lo, hi = 0, len(vals)
        while lo < hi:
            mid = (lo + hi) // 2
            if vals[mid] < value:
                lo = mid + 1
            else:
                hi = mid
        return lo / max(len(vals) - 1, 1) * 100

    out: list[dict] = []
    for s in stats:
        score = 0.0
        weight_sum = 0.0
        for field, weight, inverse in _SENTIMENT_COMPONENTS:
            value = s.get(field)
            if value is None:
                rank = 50.0
            else:
                rank = pct_rank(field, float(value))
            if inverse:
                rank = 100.0 - rank
            score += weight * rank
            weight_sum += weight
        final = round(score / weight_sum, 1)
        out.append({"score": final, "zone": _sentiment_zone(final)})
    return out

# 涨停幅度（按代码前缀）
_LIMIT_RULES = (
    (("300", "301", "688", "689"), 0.20),   # 创业板 / 科创板
    (("83", "87", "88", "92", "43"), 0.30),  # 北交所
)
_DEFAULT_LIMIT = 0.10                      # 主板
_ST_LIMIT = 0.05

SYMBOLS_FILE = DATA_DIR / "market_symbols.json"
_CACHE_DAYS = 90          # 回算窗口（自然日）
_MIN_HISTORY = 30         # 分位数标定所需最少交易日
_LOOKBACK = 60            # 返回的历史序列长度

_cache_lock = threading.Lock()
_cache: dict[str, tuple[str, dict]] = {}  # data_as_of -> (computed_at, result)


_EXCHANGE_NORMALIZE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}


def load_symbol_names() -> dict[str, str]:
    """symbol(exchange) -> name，来自同步任务落盘的清单。"""
    try:
        if SYMBOLS_FILE.is_file():
            data = json.loads(SYMBOLS_FILE.read_text(encoding="utf-8"))
            return {
                f"{it['symbol']}.{_EXCHANGE_NORMALIZE.get(it['exchange'], it['exchange'])}": it.get("name", "")
                for it in data
            }
    except Exception:  # noqa: BLE001
        pass
    return {}


def _limit_ratio(symbol: str, name: str) -> float:
    for prefixes, ratio in _LIMIT_RULES:
        if symbol.startswith(prefixes):
            return ratio
    if "ST" in name.upper():
        return _ST_LIMIT
    return _DEFAULT_LIMIT


def _limit_up_price(prev_close: float, ratio: float) -> float:
    return round(prev_close * (1 + ratio), 2)


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = p * (len(sorted_values) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _load_daily_bars(days: int) -> dict[str, list]:
    """全市场日线 {key: [bars asc]}，key = symbol.EXCHANGE。"""
    from sqlalchemy import select

    from ..db.models import BarModel
    from ..db.session import create_session

    start = datetime.now(CHINA_TZ) - timedelta(days=days)
    session = create_session()
    try:
        rows = session.scalars(
            select(BarModel).where(
                BarModel.interval == Interval.DAILY.value,
                BarModel.datetime >= start,
            )
        ).all()
    finally:
        session.close()

    by_symbol: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        by_symbol[(r.symbol, r.exchange)].append(r)
    result: dict[str, list] = {}
    for (symbol, exchange), bars in by_symbol.items():
        # 期权合约不进入全市场口径：其 thscode 自带市场后缀/连字符
        # （如 10011425.SH、IO2611-C-4000.CFE），而股票/期货 symbol
        # 均为纯代码（600519、IF2612）。期权日内波幅可达 ±50%，
        # 混入会污染异动扫描与情绪/市场状态聚合。
        if "." in symbol or "-" in symbol:
            continue
        bars.sort(key=lambda b: b.datetime)
        if len(bars) >= 2:
            result[f"{symbol}.{exchange}"] = bars
    return result


def _compute_daily_stats(
    series: dict[str, list], names: dict[str, str]
) -> list[dict]:
    """逐日聚合涨停/连板/晋级/炸板/跌停派生量。"""
    # 逐标的扫连板：daily_date -> {key: streak}
    streak_by_date: dict[Any, dict[str, int]] = {}
    touched_by_date: dict[Any, int] = defaultdict(int)   # 触及涨停
    limit_by_date: dict[Any, int] = defaultdict(int)     # 收盘涨停
    limit_down_by_date: dict[Any, int] = defaultdict(int)  # 收盘跌停

    for key, bars in series.items():
        symbol = key.rsplit(".", 1)[0]
        name = names.get(key, "")
        ratio = _limit_ratio(symbol, name)
        streak = 0
        prev_bar = None
        for bar in bars:
            if prev_bar is None or not prev_bar.close_price:
                prev_bar = bar
                continue
            prev_close = float(prev_bar.close_price)
            limit_price = _limit_up_price(prev_close, ratio)
            limit_down_price = round(prev_close * (1 - ratio), 2)
            is_limit = (
                bar.close_price
                and abs(float(bar.close_price) - limit_price) < 0.001
            )
            is_limit_down = (
                bar.close_price
                and abs(float(bar.close_price) - limit_down_price) < 0.001
            )
            touched = (
                bar.high_price
                and float(bar.high_price) >= limit_price - 0.001
            )
            date = bar.datetime.date()
            if is_limit:
                streak += 1
                limit_by_date[date] += 1
                streak_by_date.setdefault(date, {})[key] = streak
            else:
                streak = 0
            if is_limit_down:
                limit_down_by_date[date] += 1
            if touched:
                touched_by_date[date] += 1
            prev_bar = bar

    dates = sorted(set(limit_by_date) | set(touched_by_date))
    stats: list[dict] = []
    prev_pool: set[str] = set()
    for date in dates:
        streaks = streak_by_date.get(date, {})
        lu = limit_by_date[date]
        touched = touched_by_date[date]
        blast = (touched - lu) / touched if touched > 0 else None
        ge2 = sum(1 for s in streaks.values() if s >= 2)
        ge3 = sum(1 for s in streaks.values() if s >= 3)
        ge5 = sum(1 for s in streaks.values() if s >= 5)
        max_streak = max(streaks.values()) if streaks else 0
        first_board = sum(1 for s in streaks.values() if s == 1)
        # 晋级率：昨日涨停池中今日继续涨停的比例
        promoted = sum(1 for k in prev_pool if k in streaks)
        promotion = (
            round(promoted / len(prev_pool), 3) if len(prev_pool) >= 10 else None
        )
        stats.append(
            {
                "date": date.isoformat(),
                "limit_up": lu,
                "limit_down": limit_down_by_date[date],
                "touched": touched,
                "blast_rate": round(blast, 3) if blast is not None else None,
                "first_board": first_board,
                "ge2": ge2,
                "ge3": ge3,
                "ge5": ge5,
                "max_streak": max_streak,
                "promotion_rate": promotion,
                "pool_size": len(prev_pool),
                "streaks": streaks if streaks else {},
            }
        )
        prev_pool = set(streaks.keys())
    return stats


def _classify_raw(row: dict, thresholds: dict) -> str:
    """无状态的单日原始阶段判定（优先级：高潮>主升>冰点>退潮>启动>修复）。

    防抖（连续 2 日同标签才切换）在 compute_market_phase 的序列层做。
    row["_prev_lu"] 由调用方注入前一日涨停家数。
    """
    lu = float(row["limit_up"])
    height = row["max_streak"]
    promotion = row["promotion_rate"]
    prev_lu = float(row.get("_prev_lu") or 0)

    if lu >= thresholds["p85"] and height >= 5:
        return "高潮"
    if lu >= thresholds["p60"] and height >= 3 and (
        promotion is None or promotion >= 0.45
    ):
        return "主升"
    if lu <= thresholds["p10"] and height <= 2:
        return "冰点"
    if (
        prev_lu >= thresholds["p60"]
        and lu < thresholds["p40"]
        and promotion is not None
        and promotion < 0.40
    ):
        return "退潮"
    if lu >= thresholds["p25"] and prev_lu <= thresholds["p25"]:
        return "启动"
    return "修复"


def _calibrate_thresholds(lu_series: list[float]) -> dict:
    """用自身分布的分位数标定阈值（样本不足时用绝对兜底值）。"""
    ordered = sorted(lu_series)
    if len(ordered) >= _MIN_HISTORY:
        return {
            "p10": _percentile(ordered, 0.10),
            "p25": _percentile(ordered, 0.25),
            "p40": _percentile(ordered, 0.40),
            "p60": _percentile(ordered, 0.60),
            "p85": _percentile(ordered, 0.85),
        }
    # 兜底：A 股常见量级（约 5400 只）
    return {"p10": 20, "p25": 32, "p40": 42, "p60": 60, "p85": 95}


def compute_market_phase(
    lookback_days: int = _CACHE_DAYS,
    force: bool = False,
) -> dict:
    """计算连板梯队与情绪 6 阶段（带当日缓存）。"""
    series = _load_daily_bars(lookback_days)
    if len(series) < 100:
        return {
            "status": "insufficient",
            "universe_size": len(series),
            "hint": "全市场日线数据不足（需 ≥100 只标的，先在设置页跑全市场同步）",
        }

    names = load_symbol_names()
    stats = _compute_daily_stats(series, names)
    if len(stats) < _MIN_HISTORY:
        return {
            "status": "insufficient",
            "universe_size": len(series),
            "trading_days": len(stats),
            "hint": f"交易日样本不足（{len(stats)} < {_MIN_HISTORY}）",
        }

    # 分位数阈值：不含末日的历史分布（避免用今天标定今天）
    lu_history = [float(s["limit_up"]) for s in stats[:-1]]
    thresholds = _calibrate_thresholds(lu_history)

    # 6 阶段序列 + 防抖（连续 2 日同标签才切换）
    phases: list[str] = []
    current: str | None = None
    pending: str | None = None
    pending_count = 0
    prev_lu = float(stats[0]["limit_up"])
    for row in stats[1:]:
        row["_prev_lu"] = prev_lu
        raw = _classify_raw(row, thresholds)
        prev_lu = float(row["limit_up"])
        if current is None:
            current, pending, pending_count = raw, None, 0
        elif raw == current:
            pending, pending_count = None, 0
        elif raw == pending:
            pending_count += 1
            if pending_count >= 2:
                current = raw
                pending, pending_count = None, 0
        else:
            pending, pending_count = raw, 1
        phases.append(current)

    # 末日信息与梯队榜
    last = stats[-1]
    date_last = last["date"]
    streaks = last.get("streaks") or {}
    ladder = sorted(
        (
            {
                "vt_symbol": key,
                "name": names.get(key, key.rsplit(".", 1)[0]),
                "streak": streak,
            }
            for key, streak in streaks.items()
        ),
        key=lambda x: -x["streak"],
    )[:15]

    history = []
    sentiment_series = _compute_sentiment(stats)
    for row, phase, sent in zip(stats[1:], phases, sentiment_series[1:]):
        history.append(
            {
                "date": row["date"],
                "phase": phase,
                "limit_up": row["limit_up"],
                "limit_down": row["limit_down"],
                "max_streak": row["max_streak"],
                "ge2": row["ge2"],
                "promotion_rate": row["promotion_rate"],
                "score": sent["score"],
                "zone": sent["zone"],
            }
        )
    history = history[-_LOOKBACK:]

    result = {
        "status": "ok",
        "as_of": date_last,
        "universe_size": len(series),
        "trading_days": len(stats),
        "phase": phases[-1] if phases else None,
        "phase_sequence": phases[-10:],
        "sentiment_now": sentiment_series[-1],
        "sentiment_zones": [
            {"name": n, "low": low, "high": high}
            for n, low, high in SENTIMENT_ZONES
        ],
        "thresholds": {k: round(v, 1) for k, v in thresholds.items()},
        "ladder_stats": {
            "limit_up": last["limit_up"],
            "limit_down": last["limit_down"],
            "first_board": last["first_board"],
            "ge2": last["ge2"],
            "ge3": last["ge3"],
            "ge5": last["ge5"],
            "max_streak": last["max_streak"],
            "promotion_rate": last["promotion_rate"],
            "blast_rate": last["blast_rate"],
        },
        "ladder_top": ladder,
        "history": history,
        "note": "口径：全市场本地日K派生（涨停=收盘价达四舍五入涨停价；分位数阈值自适应；情绪指数=涨停/高度/晋级/炸板/2板/跌停 六分量加权百分位）",
    }

    with _cache_lock:
        _cache[date_last] = (
            datetime.now(CHINA_TZ).isoformat(),
            result,
        )
    return result


def _classify_raw(row: dict, thresholds: dict) -> str:
    """无状态的单日原始阶段判定（防抖在 compute 层做）。"""
    lu = float(row["limit_up"])
    height = row["max_streak"]
    promotion = row["promotion_rate"]
    prev_lu = float(row.get("_prev_lu") or 0)

    if lu >= thresholds["p85"] and height >= 5:
        return "高潮"
    if lu >= thresholds["p60"] and height >= 3 and (
        promotion is None or promotion >= 0.45
    ):
        return "主升"
    if lu <= thresholds["p10"] and height <= 2:
        return "冰点"
    if (
        prev_lu >= thresholds["p60"]
        and lu < thresholds["p40"]
        and promotion is not None
        and promotion < 0.40
    ):
        return "退潮"
    if lu >= thresholds["p25"] and prev_lu <= thresholds["p25"]:
        return "启动"
    return "修复"


def get_market_phase_cached(force: bool = False, max_age_minutes: int = 30) -> dict:
    """对外入口：缓存 30 分钟内直接命中，过期重算。"""
    now = datetime.now(CHINA_TZ)
    if not force:
        with _cache_lock:
            for as_of, (at, result) in list(_cache.items()):
                try:
                    age = (now - datetime.fromisoformat(at)).total_seconds() / 60
                except ValueError:
                    age = 1e9
                if age <= max_age_minutes:
                    return result
                _cache.pop(as_of, None)
    return compute_market_phase(force=force)


def peek_market_phase() -> dict | None:
    """只读缓存，不重算（供对话上下文注入——绝不能阻塞聊天请求）。"""
    with _cache_lock:
        for _as_of, (_at, result) in _cache.items():
            return result
    return None
