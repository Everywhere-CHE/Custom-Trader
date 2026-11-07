"""市场环境（Regime）：基于自选/订阅池日 K 的 4 维加权 5 档判定。

说明：TSP 的全市场口径需要全 A 股截面数据；本平台以用户真实
交易宇宙（自选池 + 已订阅合约）为样本，维度口径如下——

- trend     趋势：收盘价站上 MA20 的标的占比
- breadth   广度：最近 5 日上涨标的占比
- momentum  动量：池平均 5 日涨跌幅（[-8%, +8%] 线性归一）
- volatility 波动：池中位 20 日 ATR%（[0.5%, 6%] 反向归一，波动越低分越高）

综合分 = 0.25×trend + 0.25×breadth + 0.30×momentum + 0.20×volatility
5 档：≥70 强势 / ≥55 偏强 / ≥45 震荡 / ≥30 偏弱 / 其余 弱势。

样本不足（<3 个标的或 <20 根日 K）时如实返回 insufficient。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine

LABELS = ((70, "强势"), (55, "偏强"), (45, "震荡"), (30, "偏弱"), (0, "弱势"))

WEIGHTS = {"trend": 0.25, "breadth": 0.25, "momentum": 0.30, "volatility": 0.20}


def _norm(value: float, low: float, high: float) -> float:
    """[low, high] → [0, 100] 线性归一，越界截断。"""
    if high <= low:
        return 50.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0) * 100)


def _label(score: float) -> str:
    for threshold, label in LABELS:
        if score >= threshold:
            return label
    return "弱势"


def compute_regime(main_engine: "MainEngine", lookback_days: int = 30) -> dict:
    """计算当前 regime 与近 N 日历史（每天用当日之前的数据，无前视）。"""
    from ..core.constant import Exchange, Interval
    from ..watchlist import load_watchlist

    data_engine = main_engine.get_engine("data")
    if data_engine is None:
        raise RuntimeError("DataEngine not available")

    symbols: list[tuple[str, Exchange]] = []
    for vt_symbol in load_watchlist():
        try:
            symbol, exchange = vt_symbol.rsplit(".", 1)
            symbols.append((symbol, Exchange(exchange)))
        except ValueError:
            continue
    # 自选为空时退化为全部已订阅且本地有日线的品种
    if not symbols:
        for tick in main_engine.oms.get_all_ticks():
            try:
                symbol, exchange = tick.vt_symbol.rsplit(".", 1)
                symbols.append((symbol, Exchange(exchange)))
            except ValueError:
                continue

    # 取每只标的近 lookback+60 根日 K（预热）
    series: dict[str, list] = {}
    for symbol, exchange in symbols[:60]:  # 上限防失控
        try:
            bars = data_engine.load_bars_tail(
                symbol, exchange, Interval.DAILY, lookback_days + 60
            )
            if len(bars) >= 20:
                series[f"{symbol}.{exchange.value}"] = bars
        except Exception:  # noqa: BLE001 —— 单标的失败跳过
            continue

    if len(series) < 3:
        return {
            "status": "insufficient",
            "universe_size": len(series),
            "hint": "自选池本地日K不足（需 ≥3 个标的、每个 ≥20 根），先在行情页收藏标的并下载数据",
        }

    vt_symbols = sorted(series.keys())
    dates = sorted({b.datetime.date() for bars in series.values() for b in bars})
    recent_dates = dates[-lookback_days:]

    history: list[dict] = []
    for as_of in recent_dates:
        scores = _score_as_of(series, as_of)
        if scores is not None:
            history.append({"date": as_of.isoformat(), **scores})

    if not history:
        return {
            "status": "insufficient",
            "universe_size": len(series),
            "hint": "历史窗口不足，无法计算 regime",
        }

    current = history[-1]
    return {
        "status": "ok",
        "as_of": current["date"],
        "universe_size": len(series),
        "universe": vt_symbols,
        "score": current["score"],
        "label": current["label"],
        "dimensions": current["dimensions"],
        "history": history,
        "note": "口径：自选/订阅池样本（非全市场），维度为趋势/广度/动量/波动",
    }


def _score_as_of(series: dict[str, list], as_of) -> dict | None:
    """用 as_of 之前（不含当日）的完成态日 K 计算一次评分。"""
    ma20_above = 0
    up_5d = 0
    momentum_values: list[float] = []
    atr_values: list[float] = []
    counted = 0

    for bars in series.values():
        window = [b for b in bars if b.datetime is not None and b.datetime.date() < as_of]
        if len(window) < 21:
            continue
        closes = np.array([b.close_price for b in window], dtype=float)
        highs = np.array([b.high_price for b in window], dtype=float)
        lows = np.array([b.low_price for b in window], dtype=float)
        counted += 1

        ma20 = float(closes[-20:].mean())
        last = float(closes[-1])
        if last >= ma20:
            ma20_above += 1
        chg5 = (last / float(closes[-6]) - 1) * 100
        if chg5 > 0:
            up_5d += 1
        momentum_values.append(chg5)
        # 20 日 ATR%（Wilder 简化为均值）
        prev_close = np.concatenate(([closes[-21]], closes[-20:-1]))
        tr = np.maximum(
            highs[-20:] - lows[-20:],
            np.maximum(
                np.abs(highs[-20:] - prev_close), np.abs(lows[-20:] - prev_close)
            ),
        )
        atr_pct = float(tr.mean()) / last * 100
        atr_values.append(atr_pct)

    if counted < 3:
        return None

    trend = _norm(ma20_above / counted, 0.2, 0.8)
    breadth = _norm(up_5d / counted, 0.2, 0.8)
    momentum = _norm(float(np.mean(momentum_values)), -8.0, 8.0)
    volatility = 100.0 - _norm(float(np.median(atr_values)), 0.5, 6.0)
    score = (
        WEIGHTS["trend"] * trend
        + WEIGHTS["breadth"] * breadth
        + WEIGHTS["momentum"] * momentum
        + WEIGHTS["volatility"] * volatility
    )
    score = round(score, 1)
    return {
        "score": score,
        "label": _label(score),
        "dimensions": {
            "trend": round(trend, 1),
            "breadth": round(breadth, 1),
            "momentum": round(momentum, 1),
            "volatility": round(volatility, 1),
        },
    }
