"""个股/期货合约关键价位计算（9 大类），纯 numpy 实现，与数据层解耦。

价位分类与口径：
1. ``sr``      筹码/成交密集区 —— 换手率衰减迭代模型，POC 为最强密集区
2. ``pivot``   经典枢轴点 P / R1-3 / S1-3
3. ``swing``   前高前低 —— 60/250 日极值 + 摆动高低点
4. ``boll``    布林带（20, 2）
5. ``keltner`` Keltner 通道 短/中/长（MA20±2ATR / MA60±2.5ATR / MA120±3ATR）
6. ``atr``     ATR 波动通道（收盘 ±1/±2 ATR14）
7. ``gap``     未回补缺口中值
8. ``fib``     斐波那契回撤 0.236/0.382/0.5/0.618/0.786
9. ``psych``   整数心理关口

统一输出结构（供前端画线与 AI 工具消费）::

    {"value": float, "label": str, "type": str,
     "side": "support"|"resistance"|"pivot", "strength": 0-100}

所有函数 fail-closed：输入不足时对应类别返回空列表，绝不伪造价位。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# 摆动点判定的左右窗口
_SWING_K = 5
# 筹码分布迭代使用的最多历史 bar 数（约 3 个月筹码结构）
_CHIP_ITER_BARS = 60
# 筹码分布价格分箱数
_CHIP_BINS = 120

_LEVEL_LABELS = {
    "sr": "筹码密集区",
    "pivot": "枢轴点",
    "swing": "前高前低",
    "boll": "布林带",
    "keltner": "Keltner 通道",
    "atr": "ATR 通道",
    "gap": "未回补缺口",
    "fib": "斐波那契",
    "psych": "整数关口",
}


# ----------------------------------------------------------------------
# 基础指标
# ----------------------------------------------------------------------

def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.concatenate(([close[0]], close[:-1]))
    return np.maximum(
        high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    """Wilder ATR（末值）。"""
    if len(close) < n + 1:
        return float("nan")
    tr = _true_range(high, low, close)
    atr = tr[:n].mean()
    for v in tr[n:]:
        atr = (atr * (n - 1) + v) / n
    return float(atr)


def _sma(values: np.ndarray, n: int) -> float:
    if len(values) < n:
        return float("nan")
    return float(values[-n:].mean())


def _std(values: np.ndarray, n: int) -> float:
    if len(values) < n:
        return float("nan")
    return float(values[-n:].std(ddof=0))


# ----------------------------------------------------------------------
# 1. 筹码/成交密集区（换手率衰减模型）
# ----------------------------------------------------------------------

def _chip_levels(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    turnover: np.ndarray | None,
) -> list[dict[str, Any]]:
    """国内筹码分布模型：当日筹码 = 前日筹码×(1-换手) + 当日成交按 high~low 分摊。

    物理含义是"现在谁还拿着"，而非海外 VP 的"曾经在哪换过手"。
    无换手率数据时用 相对成交量( capped ) 作为衰减代理。
    """
    n = min(len(close), _CHIP_ITER_BARS)
    if n < 20:
        return []
    hi, lo, cl = high[-n:], low[-n:], close[-n:]
    vol = volume[-n:]

    if turnover is not None and len(turnover) == len(close) and np.isfinite(turnover[-n:]).all():
        # 换手率（比例），截断到 [0.1%, 60%] 防异常值
        decay = np.clip(turnover[-n:] / 100.0, 0.001, 0.6)
    else:
        rel = vol / max(vol.mean(), 1e-9)
        decay = np.clip(rel / 50.0, 0.005, 0.3)

    price_min = float(lo.min()) * 0.995
    price_max = float(hi.max()) * 1.005
    if price_max <= price_min:
        return []
    bins = np.linspace(price_min, price_max, _CHIP_BINS + 1)
    centers = (bins[:-1] + bins[1:]) / 2.0
    dist = np.full(_CHIP_BINS, 1.0 / _CHIP_BINS)

    for i in range(n):
        d = float(decay[i])
        if d <= 0:
            continue
        # 当日成交按 high~low 区间均匀分摊
        day_bins = np.clip(
            np.digitize([hi[i], lo[i]], bins) - 1, 0, _CHIP_BINS - 1
        )
        b_hi, b_lo = int(day_bins[0]), int(day_bins[1])
        weight = np.zeros(_CHIP_BINS)
        if b_hi <= b_lo:
            weight[b_lo] = 1.0
        else:
            weight[b_lo:b_hi + 1] = 1.0 / (b_hi - b_lo + 1)
        dist = dist * (1.0 - d) + d * weight

    total = dist.sum()
    if total <= 0:
        return []
    dist = dist / total

    poc_idx = int(dist.argmax())
    poc_value = float(centers[poc_idx])
    poc_weight = float(dist[poc_idx])

    # 取权重 ≥ 45% POC 的连续区间作为密集区（最多 3 个，按权重排序）
    threshold = poc_weight * 0.45
    mask = dist >= threshold
    clusters: list[tuple[float, float]] = []
    start: int | None = None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            clusters.append((start, i - 1))
            start = None
    if start is not None:
        clusters.append((start, len(mask) - 1))

    ranked = sorted(
        clusters,
        key=lambda c: float(dist[c[0] : c[1] + 1].sum()),
        reverse=True,
    )[:3]

    levels: list[dict[str, Any]] = []
    for c_start, c_end in ranked:
        seg = dist[c_start : c_end + 1]
        value = float((centers[c_start : c_end + 1] * seg).sum() / seg.sum())
        weight_pct = float(seg.sum())
        levels.append(
            {
                "value": round(value, 4),
                "label": f"筹码密集区({weight_pct:.0%})",
                "type": "sr",
                "strength": min(100, int(weight_pct * 250)),
                "_poc": value == poc_value,
            }
        )
    return levels


# ----------------------------------------------------------------------
# 2. 枢轴点
# ----------------------------------------------------------------------

def _pivot_levels(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[dict[str, Any]]:
    if len(close) < 2:
        return []
    p = (float(high[-2]) + float(low[-2]) + float(close[-2])) / 3.0
    rng = float(high[-2]) - float(low[-2])
    specs = [
        ("P", p, 70),
        ("R1", 2 * p - low[-2], 60), ("S1", 2 * p - high[-2], 60),
        ("R2", p + rng, 50), ("S2", p - rng, 50),
        ("R3", high[-2] + 2 * (p - low[-2]), 40),
        ("S3", low[-2] - 2 * (high[-2] - p), 40),
    ]
    return [
        {
            "value": round(float(v), 4),
            "label": f"枢轴点 {name}",
            "type": "pivot",
            "strength": s,
        }
        for name, v, s in specs
        if math.isfinite(v)
    ]


# ----------------------------------------------------------------------
# 3. 前高前低（极值 + 摆动点）
# ----------------------------------------------------------------------

def _swing_points(high: np.ndarray, low: np.ndarray, k: int = _SWING_K) -> tuple[list[int], list[int]]:
    """分形摆动点：i 点为其 ±k 窗口内的最高/最低。"""
    highs: list[int] = []
    lows: list[int] = []
    n = len(high)
    for i in range(k, n - k):
        if high[i] >= high[i - k : i + k + 1].max() and high[i] > high[i + 1 :].max(initial=-np.inf):
            highs.append(i)
        if low[i] <= low[i - k : i + k + 1].min() and low[i] < low[i + 1 :].min(initial=np.inf):
            lows.append(i)
    # 去重相邻窗口重叠的连片点，只保留每组中的极值
    return _dedup_swing(high, highs, True), _dedup_swing(low, lows, False)


def _dedup_swing(arr: np.ndarray, idxs: list[int], is_high: bool) -> list[int]:
    if not idxs:
        return []
    groups: list[list[int]] = [[idxs[0]]]
    for i in idxs[1:]:
        if i - groups[-1][-1] <= _SWING_K:
            groups[-1].append(i)
        else:
            groups.append([i])
    pick = max if is_high else min
    return [pick(g, key=lambda i: arr[i]) for g in groups]


def _swing_levels(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> list[dict[str, Any]]:
    n = len(close)
    if n < 20:
        return []
    levels: list[dict[str, Any]] = []
    # 60 / 250 日极值
    for window, tag in ((60, "60日"), (250, "250日")):
        if n >= min(window, 20):
            seg_h = high[-window:]
            seg_l = low[-window:]
            levels.append(
                {
                    "value": round(float(seg_h.max()), 4),
                    "label": f"{tag}最高",
                    "type": "swing",
                    "strength": 80 if window == 60 else 70,
                }
            )
            levels.append(
                {
                    "value": round(float(seg_l.min()), 4),
                    "label": f"{tag}最低",
                    "type": "swing",
                    "strength": 80 if window == 60 else 70,
                }
            )
    # 摆动点（最近 120 根内）
    seg = slice(max(0, n - 120), n)
    high_ids, low_ids = _swing_points(high[seg], low[seg])
    base = seg.start
    for i in high_ids[-4:]:
        levels.append(
            {
                "value": round(float(high[base + i]), 4),
                "label": "摆动高点",
                "type": "swing",
                "strength": 55,
            }
        )
    for i in low_ids[-4:]:
        levels.append(
            {
                "value": round(float(low[base + i]), 4),
                "label": "摆动低点",
                "type": "swing",
                "strength": 55,
            }
        )
    return levels


# ----------------------------------------------------------------------
# 4/5/6. 布林 / Keltner / ATR 通道
# ----------------------------------------------------------------------

def _band_levels(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    atr = _atr(high, low, close, 14)
    if math.isfinite(atr) and atr > 0:
        c = float(close[-1])
        for mult in (1.0, 2.0):
            for sign, side in ((1, "上轨"), (-1, "下轨")):
                levels.append(
                    {
                        "value": round(c + sign * mult * atr, 4),
                        "label": f"ATR{mult:g} {side}",
                        "type": "atr",
                        "strength": 40 if mult == 1 else 30,
                    }
                )
    # 布林带 (20, 2)
    mid = _sma(close, 20)
    sd = _std(close, 20)
    if math.isfinite(mid) and math.isfinite(sd) and sd > 0:
        levels += [
            {"value": round(mid + 2 * sd, 4), "label": "布林上轨", "type": "boll", "strength": 45},
            {"value": round(mid, 4), "label": "布林中轨(MA20)", "type": "boll", "strength": 50},
            {"value": round(mid - 2 * sd, 4), "label": "布林下轨", "type": "boll", "strength": 45},
        ]
    # Keltner 短/中/长
    atr20 = _atr(high, low, close, 20)
    for n, mult, tag in ((20, 2.0, "短"), (60, 2.5, "中"), (120, 3.0, "长")):
        ma = _sma(close, n)
        if math.isfinite(ma) and math.isfinite(atr20) and atr20 > 0:
            levels += [
                {
                    "value": round(ma + mult * atr20, 4),
                    "label": f"Keltner{tag}上轨",
                    "type": "keltner",
                    "strength": 35,
                },
                {
                    "value": round(ma, 4),
                    "label": f"Keltner{tag}中轨(MA{n})",
                    "type": "keltner",
                    "strength": 40,
                },
                {
                    "value": round(ma - mult * atr20, 4),
                    "label": f"Keltner{tag}下轨",
                    "type": "keltner",
                    "strength": 35,
                },
            ]
    return levels


# ----------------------------------------------------------------------
# 7. 未回补缺口
# ----------------------------------------------------------------------

def _gap_levels(high: np.ndarray, low: np.ndarray) -> list[dict[str, Any]]:
    n = len(high)
    if n < 10:
        return []
    levels: list[dict[str, Any]] = []
    for i in range(max(1, n - 120), n):
        prev_high = float(high[i - 1])
        prev_low = float(low[i - 1])
        if float(low[i]) > prev_high:
            gap_mid = (float(low[i]) + prev_high) / 2.0
            direction, name = 1, "向上缺口"
        elif float(high[i]) < prev_low:
            gap_mid = (float(high[i]) + prev_low) / 2.0
            direction, name = -1, "向下缺口"
        else:
            continue
        # 已回补判定：之后任一 bar 触及缺口中值即视为回补
        if direction == 1 and low[i + 1 :].min(initial=np.inf) <= gap_mid:
            continue
        if direction == -1 and high[i + 1 :].max(initial=-np.inf) >= gap_mid:
            continue
        levels.append(
            {
                "value": round(gap_mid, 4),
                "label": f"{name}(未回补)",
                "type": "gap",
                "strength": 50,
            }
        )
    return levels


# ----------------------------------------------------------------------
# 8. 斐波那契回撤
# ----------------------------------------------------------------------

_FIB_RATIOS = ((0.236, 40), (0.382, 55), (0.5, 60), (0.618, 65), (0.786, 45))


def _fib_levels(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> list[dict[str, Any]]:
    n = len(close)
    if n < 30:
        return []
    seg_h = float(high[-60:].max())
    seg_l = float(low[-60:].min())
    if seg_h <= seg_l:
        return []
    swing = seg_h - seg_l
    # 以最近 60 日主方向为基准：上涨段回撤支撑 / 下跌段反弹阻力
    if close[-1] >= (seg_h + seg_l) / 2.0:
        anchor_from, anchor_to, kind = seg_h, seg_l, "支撑"
    else:
        anchor_from, anchor_to, kind = seg_l, seg_h, "阻力"
    levels: list[dict[str, Any]] = []
    for ratio, strength in _FIB_RATIOS:
        value = anchor_from - (anchor_from - anchor_to) * ratio
        levels.append(
            {
                "value": round(float(value), 4),
                "label": f"Fib {ratio:g} {kind}",
                "type": "fib",
                "strength": strength,
            }
        )
    del swing
    return levels


# ----------------------------------------------------------------------
# 9. 整数心理关口
# ----------------------------------------------------------------------

def _psych_levels(close_last: float, atr: float) -> list[dict[str, Any]]:
    p = abs(close_last)
    if p <= 0:
        return []
    if p < 2:
        step = 0.1
    elif p < 10:
        step = 0.5
    elif p < 50:
        step = 1.0
    elif p < 200:
        step = 2.5
    elif p < 1000:
        step = 5.0
    else:
        step = 10.0
    span = max(atr if math.isfinite(atr) and atr > 0 else p * 0.03, p * 0.005) * 4
    lo_bound = close_last - span
    hi_bound = close_last + span
    above: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    k = math.ceil(lo_bound / step)
    while k * step <= hi_bound:
        value = round(k * step, 6)
        if value > 0:
            # 整除性越好（尾随零越多），心理强度越高
            roundness = 0
            probe = value / step
            while abs(probe - round(probe)) < 1e-9 and round(probe) % 5 == 0 and roundness < 3:
                probe /= 5
                roundness += 1
            level = {
                "value": value,
                "label": f"整数关口 {value:g}",
                "type": "psych",
                "strength": 35 + roundness * 10,
            }
            if value > close_last:
                above.append(level)
            else:
                below.append(level)
        k += 1
    # 只保留距现价最近的上下各 2 档，防止密集成排
    above.sort(key=lambda x: x["value"])
    below.sort(key=lambda x: -x["value"])
    return above[:2] + below[:2]


# ----------------------------------------------------------------------
# 汇总入口
# ----------------------------------------------------------------------

def compute_key_levels(
    bars: list[dict[str, Any]],
    max_levels: int = 24,
) -> dict[str, Any]:
    """从日 K 列表计算 9 大类关键价位。

    Args:
        bars: 按 [{open, high, low, close, volume, turnover_rate?, ...}] 升序。
        max_levels: 去重后返回的最大价位数（按 |close - value| 升序截断）。

    Returns:
        {"levels": [...], "reference": {...}}，数据不足时 levels 可能为空。
    """
    if not bars or len(bars) < 10:
        return {"levels": [], "reference": {"close": None, "atr": None, "bars_used": len(bars or [])}}

    high = np.array([float(b["high"]) for b in bars], dtype=float)
    low = np.array([float(b["low"]) for b in bars], dtype=float)
    close = np.array([float(b["close"]) for b in bars], dtype=float)
    volume = np.array([float(b.get("volume") or 0) for b in bars], dtype=float)
    turnover = None
    tr_values = [b.get("turnover_rate") for b in bars]
    if all(isinstance(v, (int, float)) and v is not None and v == v for v in tr_values):
        turnover = np.array(tr_values, dtype=float)

    last_close = float(close[-1])
    atr = _atr(high, low, close, 14)

    raw_levels: list[dict[str, Any]] = []
    raw_levels += _chip_levels(high, low, close, volume, turnover)
    raw_levels += _pivot_levels(high, low, close)
    raw_levels += _swing_levels(high, low, close)
    raw_levels += _band_levels(high, low, close)
    raw_levels += _gap_levels(high, low)
    raw_levels += _fib_levels(high, low, close)
    raw_levels += _psych_levels(last_close, atr)

    # side 标注：相对现价的方向（±0.15 ATR 内视为中枢）
    if math.isfinite(atr) and atr > 0:
        pivot_zone = 0.15 * atr
    else:
        pivot_zone = last_close * 0.002

    merged: list[dict[str, Any]] = []
    for lv in raw_levels:
        value = float(lv["value"])
        if not math.isfinite(value) or value <= 0:
            continue
        diff = value - last_close
        if abs(diff) <= pivot_zone:
            side = "pivot"
        elif diff < 0:
            side = "support"
        else:
            side = "resistance"
        merged.append(
            {
                "value": value,
                "label": lv["label"],
                "type": lv["type"],
                "side": side,
                "strength": int(lv["strength"]),
                "distance_pct": round(diff / last_close * 100, 2),
            }
        )

    # 近重合并：价差 < max(0.1 ATR, 0.1%) 的价位保留 strength 较高者
    merge_tol = max(atr * 0.1 if math.isfinite(atr) and atr > 0 else 0.0, last_close * 0.001)
    merged.sort(key=lambda x: (-x["strength"], abs(x["distance_pct"])))
    deduped: list[dict[str, Any]] = []
    for lv in merged:
        if all(abs(lv["value"] - d["value"]) > merge_tol for d in deduped):
            deduped.append(lv)

    # 每类上限 3 条，防止同类价位成排（如 Keltner 三档 × 短中长全上）
    type_counts: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for lv in deduped:
        t = lv["type"]
        if type_counts.get(t, 0) >= 3:
            continue
        type_counts[t] = type_counts.get(t, 0) + 1
        capped.append(lv)

    capped.sort(key=lambda x: abs(x["distance_pct"]))
    capped = capped[: min(max_levels, 16)]
    deduped = capped
    for rank, lv in enumerate(deduped, 1):
        lv["rank"] = rank

    supports = sum(1 for lv in deduped if lv["side"] == "support")
    resistances = sum(1 for lv in deduped if lv["side"] == "resistance")
    return {
        "levels": deduped,
        "reference": {
            "close": round(last_close, 4),
            "atr": round(atr, 4) if math.isfinite(atr) else None,
            "bars_used": len(bars),
            "support_count": supports,
            "resistance_count": resistances,
            "categories": _LEVEL_LABELS,
        },
    }


# ----------------------------------------------------------------------
# 平台入口（带数据补拉）：关注页/路由共用
# ----------------------------------------------------------------------

def compute_key_levels_for(main_engine, vt_symbol: str, days: int = 250) -> dict:  # noqa: ANN001
    """给定 vt_symbol 计算关键价位；本地日K不足 60 根时自动经网关
    补拉历史（FUYAO 已连接时优先走 FUYAO）。数据不足抛 ValueError。
    """
    from datetime import datetime, timedelta

    from ..api.routes.market import _extract_vt_symbol_or_raise
    from ..core.constant import Interval
    from ..core.object import HistoryRequest
    from ..db.data_engine import DataEngine

    try:
        symbol, exchange = _extract_vt_symbol_or_raise(vt_symbol)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(exc)) from None

    data_engine = main_engine.get_engine("data")
    if not isinstance(data_engine, DataEngine):
        raise RuntimeError("DataEngine not available")

    bars = data_engine.load_bars_tail(symbol, exchange, Interval.DAILY, days)
    # 期权合约兜底：本地无日K时经 fuyao 期权接口补拉
    if len(bars) < 10:
        try:
            from datetime import datetime as _dt, timedelta as _td

            from .option_sync import ensure_option_bars_for_symbol

            fallback = ensure_option_bars_for_symbol(
                data_engine,
                symbol,
                exchange.value,
                _dt.now() - _td(days=max(days + 30, 400)),
                _dt.now(),
            )
            if fallback:
                data_engine.save_bars(fallback)
                # 注意：期权日K以 thscode 入库（symbol 带市场后缀），
                # 不能按原 CTP 代码重读，直接使用兜底返回的 bars
                bars = fallback
        except Exception:  # noqa: BLE001 —— 兜底失败按原逻辑走
            pass
    if len(bars) < 60:
        contract = main_engine.oms.get_contract(vt_symbol)
        if contract and contract.history_data:
            req = HistoryRequest(
                symbol=symbol,
                exchange=exchange,
                start=datetime.now() - timedelta(days=max(days + 30, 400)),
                end=datetime.now(),
                interval=Interval.DAILY,
            )
            downloaded = main_engine.query_history(req, contract.gateway_name)
            if downloaded:
                data_engine.save_bars(downloaded)
                bars = data_engine.load_bars_tail(
                    symbol, exchange, Interval.DAILY, days
                )

    if len(bars) < 10:
        raise ValueError(f"日线数据不足（{len(bars)} 根），无法计算关键价位")

    payload = [
        {
            "open": b.open_price,
            "high": b.high_price,
            "low": b.low_price,
            "close": b.close_price,
            "volume": b.volume,
        }
        for b in bars
        if b.close_price
    ]
    result = compute_key_levels(payload)
    result["vt_symbol"] = vt_symbol
    return result
