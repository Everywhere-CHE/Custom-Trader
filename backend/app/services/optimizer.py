"""参数优化器：稳健参数筛选 + Walk-Forward 滚动样本外检验。

设计纪律（借鉴 ARL/TSP 研究栈，fail-closed）：

- 每段评估都是**完整独立回测**（含 30 天预热），段与段互不污染；
- 段末强制平仓（``BacktestingEngine.close_at_end``），保证每段统计自洽；
- walk-forward 的 OOS 段紧接训练段**次日**开始，同一根 K 线绝不
  同时进入 IS 与 OOS（防止前视泄漏）；
- 稳健分 ``R(p) = 0.6 × median(段得分) + 0.4 × worst(段得分)``——
  中位数扛单段运气，worst 惩罚"只在一段行情好用"的孤峰参数；
- 得分统一"越高越好"方向（max_drawdown 本身为负值，天然可用）；
- 失败/无数据的组合如实记录为错误行，不伪装成 0 分混入排名。
"""

from __future__ import annotations

import statistics as _stats
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..core.constant import Interval
from ..core.strategy.backtesting import BacktestingEngine

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..core.engine.main_engine import MainEngine

# 允许用作优化目标的统计字段（全部"越高越好"）
METRICS = (
    "sharpe_ratio",
    "total_return",
    "annual_return",
    "return_drawdown_ratio",
    "total_net_pnl",
    "max_ddpercent",
)
WORST_SCORE = float("-inf")

# 网格与折数上限（防误操作把服务跑挂）
MAX_COMBOS = 200
MAX_FOLDS = 12


# ----------------------------------------------------------------------
# 网格展开
# ----------------------------------------------------------------------

def expand_grid(grid: dict[str, Any]) -> list[dict]:
    """把参数网格展开为组合列表。

    取值形式：
    - ``{"n": [5, 10, 20]}`` 枚举；
    - ``{"n": {"start": 5, "end": 60, "step": 5}}`` 等差区间；
    - 混合使用，笛卡尔积。
    """
    if not grid:
        return [{}]
    axes: list[list[tuple[str, Any]]] = []
    for name, spec in grid.items():
        values: list[Any]
        if isinstance(spec, dict):
            start = spec.get("start")
            end = spec.get("end")
            step = spec.get("step") or 1
            if start is None or end is None:
                raise ValueError(f"参数 {name!r} 区间缺少 start/end")
            values = []
            cur = start
            guard = 0
            while cur <= end and guard < 1000:
                values.append(round(cur, 6))
                cur += step
                guard += 1
            if not values:
                raise ValueError(f"参数 {name!r} 区间为空")
        elif isinstance(spec, list):
            values = list(spec)
        else:
            values = [spec]
        if not values:
            raise ValueError(f"参数 {name!r} 取值列表为空")
        axes.append([(name, v) for v in values])

    combos: list[dict] = [{}]
    for axis in axes:
        combos = [{**c, **dict([pair])} for c in combos for pair in axis]
        if len(combos) > MAX_COMBOS:
            raise ValueError(
                f"参数组合数超过上限 {MAX_COMBOS}（当前 {len(combos)}），请缩小网格"
            )
    return combos


@dataclass
class Segment:
    """一个回测窗口段（自然日切分，engine 自行按数据对齐）。"""

    start: datetime
    end: datetime

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def split_segments(
    start: datetime, end: datetime, k: int, min_days: int = 14
) -> list[Segment]:
    """把 [start, end] 等分为 K 个互不重叠的验证段。"""
    total_days = (end - start).days
    if total_days < k * min_days:
        raise ValueError(
            f"回测区间 {total_days} 天不足以切出 {k} 段（每段至少 {min_days} 天）"
        )
    seg_len = total_days // k
    segments: list[Segment] = []
    seg_start = start
    for i in range(k):
        seg_end = start + timedelta(days=seg_len * (i + 1)) if i < k - 1 else end
        segments.append(Segment(seg_start, seg_end))
        seg_start = seg_end + timedelta(days=1)
    return segments


def generate_folds(
    start: datetime,
    end: datetime,
    train_days: int = 252,
    test_days: int = 63,
    step_days: int = 63,
) -> list[tuple[Segment, Segment]]:
    """滚动折：训练段后**次日**起为测试段（隔断 IS/OOS 重叠）。"""
    if (end - start).days < train_days + test_days:
        raise ValueError(
            f"回测区间 {(end - start).days} 天不足以切出一折"
            f"（train {train_days} + test {test_days}）"
        )
    folds: list[tuple[Segment, Segment]] = []
    offset = 0
    while True:
        train_start = start + timedelta(days=offset)
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end + timedelta(days=1)
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        folds.append((Segment(train_start, train_end), Segment(test_start, test_end)))
        offset += step_days
        if len(folds) >= MAX_FOLDS:
            break
    if not folds:
        raise ValueError("折数不足：请调整 train/test 天数或扩大回测区间")
    return folds


# ----------------------------------------------------------------------
# 单次回测
# ----------------------------------------------------------------------

def run_once(
    main_engine: "MainEngine",
    strategy_class: type,
    vt_symbol: str,
    interval: Interval,
    seg: Segment,
    setting: dict,
    cost: dict,
) -> dict:
    """跑一次完整回测（段末强平），返回统计字段。无数据/爆仓照常返回统计。"""
    data_engine = main_engine.get_engine("data")
    if data_engine is None:
        raise RuntimeError("DataEngine not available")

    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=vt_symbol,
        interval=interval,
        start=seg.start,
        end=seg.end,
        rate=cost.get("rate", 0.0001),
        slippage=cost.get("slippage", 0),
        size=cost.get("size", 1),
        pricetick=cost.get("pricetick", 0.01),
        capital=cost.get("capital", 1_000_000),
    )
    engine.add_strategy(strategy_class, dict(setting))
    engine.load_data(data_engine)
    if not engine.history_data:
        raise ValueError("该区间无历史数据")
    engine.run_backtesting()
    engine.close_at_end()
    return engine.calculate_statistics()


def run_option_once(
    main_engine: "MainEngine",
    strategy_class: type,
    underlying: str,
    interval: "Interval",
    seg: "Segment",
    setting: dict,
    cost: dict,
) -> dict:
    """期权组合模板的单次回测（与 :func:`run_once` 同参签名，供
    robust/walk_forward 经 ``runner`` 注入复用）。

    ``interval`` 忽略（期权回测固定日线）；``underlying`` 即
    run_once 的 ``vt_symbol`` 位置。无数据/爆仓照常返回统计。
    """
    from ..core.strategy.option_backtesting import OptionBacktestingEngine

    data_engine = main_engine.get_engine("data")
    if data_engine is None:
        raise RuntimeError("DataEngine not available")

    engine = OptionBacktestingEngine()
    engine.set_parameters(
        underlying=underlying,
        start=seg.start,
        end=seg.end,
        capital=cost.get("capital", 1_000_000),
        commission=cost.get("commission", 0),
        slippage=cost.get("slippage", 0),
        risk_free=cost.get("risk_free", 0.02),
        iv_fallback=cost.get("iv_fallback", 0.25),
        margin_coef=cost.get("margin_coef"),
        margin_limit_pct=cost.get("margin_limit_pct", 0.8),
        strike_range_pct=cost.get("strike_range_pct", 0.25),
    )
    engine.add_strategy(strategy_class, dict(setting))
    if not engine.load_data(data_engine):
        raise ValueError("该区间无期权数据（请先在期权页同步）")
    engine.run_backtesting()
    return engine.calculate_statistics()


def _run_seg(
    main_engine: "MainEngine",
    strategy_class: type,
    vt_symbol: str,
    interval: "Interval",
    seg: "Segment",
    setting: dict,
    cost: dict,
    runner: "Callable | None" = None,
) -> dict:
    """段级回测分发：runner 为空走单标的 run_once，否则注入的
    期权 runner（签名兼容，underlying 占用 vt_symbol 位）。"""
    if runner is None:
        return run_once(
            main_engine, strategy_class, vt_symbol, interval,
            seg, setting, cost,
        )
    return runner(
        main_engine, strategy_class, vt_symbol, interval,
        seg, setting, cost,
    )


def _score(stats: dict, metric: str) -> float:
    value = stats.get(metric)
    if value is None:
        return WORST_SCORE
    try:
        return float(value)
    except (TypeError, ValueError):
        return WORST_SCORE


def _fmt_params(params: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items()) or "(默认参数)"


# ----------------------------------------------------------------------
# 稳健参数筛选
# ----------------------------------------------------------------------

def robust_optimize(
    main_engine: "MainEngine",
    strategy_class: type,
    vt_symbol: str,
    interval: Interval,
    start: datetime,
    end: datetime,
    grid: dict[str, Any],
    base_setting: dict,
    cost: dict,
    metric: str = "sharpe_ratio",
    k_segments: int = 3,
    progress=None,
    runner: "Callable | None" = None,
) -> dict:
    """K 段等分验证窗稳健优化。

    全网格 × K 段独立回测 → 每个参数组合得到 K 个段得分 →
    按稳健分排名；冠军邻域分析给出过拟合预警。
    """
    if metric not in METRICS:
        raise ValueError(f"不支持的优化目标 {metric!r}，可选: {', '.join(METRICS)}")
    combos = expand_grid(grid)
    segments = split_segments(start, end, k_segments)
    total = len(combos) * len(segments)

    rows: list[dict] = []
    done = 0
    for params in combos:
        seg_scores: list[float] = []
        seg_detail: list[Any] = []
        errors: list[str] = []
        for seg in segments:
            try:
                stats = _run_seg(
                    main_engine, strategy_class, vt_symbol, interval,
                    seg, {**base_setting, **params}, cost, runner,
                )
                seg_scores.append(_score(stats, metric))
                seg_detail.append(
                    {"segment": seg.to_dict(), "score": seg_scores[-1],
                     "trades": stats.get("total_trade_count", 0),
                     "total_return": stats.get("total_return", 0.0),
                     "max_ddpercent": stats.get("max_ddpercent", 0.0)}
                )
            except Exception as exc:  # noqa: BLE001 —— 单组合失败不拖垮整轮
                seg_scores.append(WORST_SCORE)
                seg_detail.append(
                    {"segment": seg.to_dict(), "error": f"{type(exc).__name__}: {exc}"}
                )
                errors.append(f"{seg.start:%m-%d}: {exc}")
            done += 1
            if progress and done % 5 == 0:
                progress(f"稳健优化 {done}/{total}")

        valid = [s for s in seg_scores if s != WORST_SCORE]
        median = _stats.median(valid) if valid else WORST_SCORE
        worst = min(valid) if valid else WORST_SCORE
        mean = _stats.fmean(valid) if valid else WORST_SCORE
        rows.append(
            {
                "params": params,
                "params_text": _fmt_params(params),
                "segment_scores": seg_scores,
                "segment_detail": seg_detail,
                "median": median,
                "worst": worst,
                "mean": mean,
                "robust_score": (
                    0.6 * median + 0.4 * worst if valid else WORST_SCORE
                ),
                "valid_segments": len(valid),
                "errors": errors,
            }
        )

    # 排名：先按稳健分，再按中位数（并列时偏好均值更好）
    def sort_key(r: dict) -> tuple:
        return (
            r["robust_score"] if r["robust_score"] != WORST_SCORE else float("-1e18"),
            r["median"] if r["median"] != WORST_SCORE else float("-1e18"),
            r["mean"],
        )

    rows.sort(key=sort_key, reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    valid_rows = [r for r in rows if r["robust_score"] != WORST_SCORE]

    result: dict[str, Any] = {
        "mode": "robust",
        "metric": metric,
        "segments": [s.to_dict() for s in segments],
        "k_segments": len(segments),
        "total_combos": len(combos),
        "total_runs": total,
        "results": rows[:20],
        "valid_count": len(valid_rows),
        "error_count": len(rows) - len(valid_rows),
        "robust_formula": "0.6 × median(段得分) + 0.4 × worst(段得分)",
    }

    if not valid_rows:
        result["champion"] = None
        return result

    champion = valid_rows[0]
    champ_score = champion["robust_score"]

    # ① 冠军收敛度：各段 argmax 是否同一组参数
    argmax_per_seg: list[str] = []
    for si in range(len(segments)):
        best_row, best_v = None, WORST_SCORE
        for r in valid_rows:
            v = r["segment_scores"][si]
            if v != WORST_SCORE and v > best_v:
                best_row, best_v = r, v
        argmax_per_seg.append(
            best_row["params_text"] if best_row else "(无有效)"
        )
    distinct = set(argmax_per_seg)
    if len(distinct) == 1:
        convergence = "unanimous"
    elif len(distinct) <= max(2, len(segments) // 2):
        convergence = "majority"
    else:
        convergence = "scattered"

    # ② 参数高原：冠军网格邻域（单参数移动一步）衰减评估
    neighbors: list[dict] = []
    for name, value in champion["params"].items():
        spec = grid.get(name)
        if isinstance(spec, list) and len(spec) > 1:
            idx = spec.index(value) if value in spec else None
            if idx is None:
                continue
            for j in (idx - 1, idx + 1):
                if 0 <= j < len(spec):
                    neighbors.append({**champion["params"], name: spec[j]})
        elif isinstance(spec, dict):
            step = spec.get("step") or 1
            neighbors.append({**champion["params"], name: value + step})
            neighbors.append({**champion["params"], name: value - step})
    # 只保留网格内真实存在（重展开可命中）的邻居
    combo_keys = {_fmt_params(c) for c in combos}
    neighbors = [n for n in neighbors if _fmt_params(n) in combo_keys]

    neighbor_scores: list[dict] = []
    for n in neighbors[:16]:
        match = next(
            (r for r in valid_rows if r["params_text"] == _fmt_params(n)), None
        )
        if match is not None:
            neighbor_scores.append(
                {"params_text": _fmt_params(n), "robust_score": match["robust_score"]}
            )

    scores_all = [r["robust_score"] for r in valid_rows]
    tol = max(abs(champ_score) * 0.05, (max(scores_all) - min(scores_all)) * 0.05) if len(scores_all) > 1 else 0.0
    plateau_count = sum(1 for n in neighbor_scores if champ_score - n["robust_score"] <= tol)
    plateau_ratio = plateau_count / len(neighbor_scores) if neighbor_scores else None
    if plateau_ratio is None:
        plateau = "unknown"
    elif plateau_ratio >= 0.6:
        plateau = "plateau"
    elif plateau_ratio <= 0.001:
        plateau = "peak"
    else:
        plateau = "ridge"

    # ③ 样本内乐观偏差：冠军全窗回测 vs 稳健分
    try:
        full_stats = _run_seg(
            main_engine, strategy_class, vt_symbol, interval,
            Segment(start, end), {**base_setting, **champion["params"]}, cost,
            runner,
        )
        full_score = _score(full_stats, metric)
    except Exception as exc:  # noqa: BLE001
        full_stats, full_score = None, None
        result["champion_full_window_error"] = str(exc)

    result["champion"] = {
        "params": champion["params"],
        "params_text": champion["params_text"],
        "robust_score": champ_score,
        "median": champion["median"],
        "worst": champion["worst"],
        "valid_segments": champion["valid_segments"],
        "convergence": convergence,
        "argmax_per_segment": argmax_per_seg,
        "plateau": plateau,
        "plateau_ratio": plateau_ratio,
        "neighbor_scores": neighbor_scores,
        "full_window_score": full_score,
        "full_window_stats": full_stats,
        "in_sample_bias": (
            round(full_score - champ_score, 4)
            if full_score is not None and full_score != WORST_SCORE
            else None
        ),
        "notes": _champion_notes(convergence, plateau),
    }
    return result


def _champion_notes(convergence: str, plateau: str) -> list[str]:
    notes: list[str] = []
    if convergence == "unanimous":
        notes.append("各段最优参数一致，参数选择对分段不敏感")
    elif convergence == "majority":
        notes.append("多数段最优参数收敛，存在少量分歧")
    else:
        notes.append("各段最优参数分散——参数稳定性差，谨慎采用")
    if plateau == "plateau":
        notes.append("冠军邻域表现接近（参数高原），过拟合风险较低")
    elif plateau == "peak":
        notes.append("冠军邻域全面衰减（孤峰），过拟合风险高")
    elif plateau == "ridge":
        notes.append("冠军邻域部分衰减，建议观察相邻参数的稳健分")
    return notes


# ----------------------------------------------------------------------
# Walk-Forward 滚动样本外
# ----------------------------------------------------------------------

def walk_forward(
    main_engine: "MainEngine",
    strategy_class: type,
    vt_symbol: str,
    interval: Interval,
    start: datetime,
    end: datetime,
    grid: dict[str, Any],
    base_setting: dict,
    cost: dict,
    metric: str = "sharpe_ratio",
    train_days: int = 252,
    test_days: int = 63,
    step_days: int = 63,
    progress=None,
    runner: "Callable | None" = None,
) -> dict:
    """滚动折：训练段网格选参 → 测试段 OOS 验证。

    产出：各折 OOS 统计、退化度（IS 均值 − OOS 均值）、
    一致性（OOS 为正的折占比）。失败折透明列出，不伪装成 0 收益。
    """
    if metric not in METRICS:
        raise ValueError(f"不支持的优化目标 {metric!r}，可选: {', '.join(METRICS)}")
    combos = expand_grid(grid)
    folds = generate_folds(start, end, train_days, test_days, step_days)

    fold_rows: list[dict] = []
    is_scores: list[float] = []
    oos_scores: list[float] = []
    oos_returns: list[float] = []
    oos_trades = 0
    done = 0
    total_runs = len(folds) * len(combos) + len(folds)

    for fi, (train_seg, test_seg) in enumerate(folds, 1):
        # 训练段网格
        best_params, best_score, best_stats = None, WORST_SCORE, None
        grid_errors: list[str] = []
        for params in combos:
            try:
                stats = _run_seg(
                    main_engine, strategy_class, vt_symbol, interval,
                    train_seg, {**base_setting, **params}, cost, runner,
                )
                s = _score(stats, metric)
                if s != WORST_SCORE and (best_params is None or s > best_score):
                    best_params, best_score, best_stats = params, s, stats
            except Exception as exc:  # noqa: BLE001
                grid_errors.append(f"{_fmt_params(params)}: {exc}")
            done += 1
            if progress and done % 5 == 0:
                progress(f"Walk-Forward 折{fi}/{len(folds)} 训练 {done}/{total_runs}")

        if best_params is None:
            fold_rows.append(
                {
                    "fold": fi,
                    "train": train_seg.to_dict(),
                    "test": test_seg.to_dict(),
                    "status": "train_failed",
                    "errors": grid_errors[:5],
                }
            )
            continue

        # OOS 段：固定冠军参数
        try:
            oos_stats = _run_seg(
                main_engine, strategy_class, vt_symbol, interval,
                test_seg, {**base_setting, **best_params}, cost, runner,
            )
            oos_score = _score(oos_stats, metric)
        except Exception as exc:  # noqa: BLE001
            fold_rows.append(
                {
                    "fold": fi,
                    "train": train_seg.to_dict(),
                    "test": test_seg.to_dict(),
                    "status": "oos_failed",
                    "best_params": best_params,
                    "best_params_text": _fmt_params(best_params),
                    "is_score": best_score,
                    "errors": [str(exc)],
                }
            )
            continue
        done += 1

        is_scores.append(best_score)
        oos_scores.append(oos_score)
        oos_returns.append(float(oos_stats.get("total_return", 0.0)))
        oos_trades += int(oos_stats.get("total_trade_count", 0))
        fold_rows.append(
            {
                "fold": fi,
                "train": train_seg.to_dict(),
                "test": test_seg.to_dict(),
                "status": "ok",
                "best_params": best_params,
                "best_params_text": _fmt_params(best_params),
                "is_score": best_score,
                "oos_score": oos_score,
                "oos_total_return": oos_stats.get("total_return"),
                "oos_sharpe": oos_stats.get("sharpe_ratio"),
                "oos_max_ddpercent": oos_stats.get("max_ddpercent"),
                "oos_trades": oos_stats.get("total_trade_count"),
            }
        )
        if progress:
            progress(f"Walk-Forward 折{fi}/{len(folds)} 完成")

    valid = [r for r in fold_rows if r["status"] == "ok"]
    is_mean = _stats.fmean(is_scores) if is_scores else None
    oos_mean = _stats.fmean(oos_scores) if oos_scores else None
    degradation = (
        round(is_mean - oos_mean, 4)
        if is_mean is not None and oos_mean is not None
        else None
    )
    consistency = (
        sum(1 for s in oos_scores if s > 0) / len(oos_scores) if oos_scores else None
    )

    return {
        "mode": "walkforward",
        "metric": metric,
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days,
        "total_combos": len(combos),
        "folds": fold_rows,
        "valid_folds": len(valid),
        "failed_folds": len(fold_rows) - len(valid),
        "is_mean": is_mean,
        "oos_mean": oos_mean,
        "degradation": degradation,
        "consistency": consistency,
        "oos_avg_return": (
            _stats.fmean(oos_returns) if oos_returns else None
        ),
        "oos_total_trades": oos_trades,
        "notes": _wf_notes(degradation, consistency),
    }


def _wf_notes(degradation: float | None, consistency: float | None) -> list[str]:
    notes: list[str] = []
    if degradation is not None:
        if degradation > 0:
            notes.append(
                f"OOS 均值较 IS 低 {degradation:.2f}——存在过拟合衰减，"
                "衰减越大说明训练段选出的参数越依赖样本内行情"
            )
        else:
            notes.append("OOS 均值不低于 IS——样本外表现稳健")
    if consistency is not None:
        if consistency >= 0.6:
            notes.append(f"{consistency:.0%} 的折 OOS 为正，跨期一致性较好")
        else:
            notes.append(f"仅 {consistency:.0%} 的折 OOS 为正，跨期一致性弱")
    return notes
