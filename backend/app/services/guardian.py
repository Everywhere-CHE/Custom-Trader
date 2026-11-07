"""策略守护（Guardian）：发布后策略用新数据定期复验，退化自动三态判定。

三态状态机：``healthy ⇄ watch → retired``。

复验纪律（借鉴 ARL/TSP，逐层样本外）：
- 只在**基线 data_as_of 之后的新数据**上复验，结果绝不写回基线；
- 新交易日不足 30 根跳过（太短切窗是噪声，宁跳过防假告警）；
- 新窗口切 2~4 段等分验证段，逐段回测取稳健分
  （``0.6 × median + 0.4 × worst``，与优化器同口径）；
- 退化判定（方向感知）：``pct = (baseline − current) / baseline``，
  pct > 20% → watch，pct > 50% → retire；
- 硬条件：交易数枯竭（0 笔）→ 直接退役；
- 数据残缺/回测报错按"最多降 watch"处理（incomplete 不退役）；
- 退役只标记不删档，可随时 drop 或重新 adopt。

档案存储 ``data/guardian.json``，原子写。
"""

from __future__ import annotations

import hashlib
import json
import statistics as _stats
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR
from ..core.constant import Interval
from ..core.utility import CHINA_TZ, extract_vt_symbol
from .optimizer import METRICS, Segment, run_once

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine

GUARDIAN_FILE = DATA_DIR / "guardian.json"
WATCH_PCT = 0.20
RETIRE_PCT = 0.50
MIN_NEW_BARS = 30
MAX_CHECKS = 52

_lock = threading.Lock()


# ----------------------------------------------------------------------
# 档案读写
# ----------------------------------------------------------------------

def _load_all() -> dict[str, dict]:
    if not GUARDIAN_FILE.is_file():
        return {}
    try:
        return json.loads(GUARDIAN_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 损坏档案按空处理，避免拖垮启动
        return {}


def _save_all(data: dict[str, dict]) -> None:
    GUARDIAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GUARDIAN_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(GUARDIAN_FILE)


def make_guardian_id(class_name: str, vt_symbol: str, setting: dict) -> str:
    sig = json.dumps(setting, ensure_ascii=False, sort_keys=True)
    digest = hashlib.md5(sig.encode()).hexdigest()[:6]
    return f"{class_name}_{vt_symbol.replace('.', '_')}_{digest}"


def list_guardians() -> list[dict]:
    with _lock:
        records = list(_load_all().values())
    records.sort(key=lambda r: (r.get("status") != "watch", r.get("status") != "healthy"))
    return records


def get_guardian(guardian_id: str) -> dict | None:
    with _lock:
        return _load_all().get(guardian_id)


# ----------------------------------------------------------------------
# 建档 / 复验 / 删除
# ----------------------------------------------------------------------

def adopt(
    main_engine: "MainEngine",
    strategy_class: type,
    class_name: str,
    vt_symbol: str,
    interval: Interval,
    setting: dict,
    cost: dict,
    baseline_start: datetime,
    baseline_end: datetime,
    metric: str = "sharpe_ratio",
) -> dict:
    """用给定区间的回测结果建立守护基线，状态置 healthy。"""
    if metric not in METRICS:
        raise ValueError(f"不支持的守护目标 {metric!r}，可选: {', '.join(METRICS)}")
    stats = run_once(
        main_engine, strategy_class, vt_symbol, interval,
        Segment(baseline_start, baseline_end), setting, cost,
    )
    if not stats or stats.get("total_days", 0) < 10:
        raise ValueError("基线区间数据不足（<10 个交易日），请扩大区间或先下载数据")

    data_as_of = stats.get("end_date")
    if hasattr(data_as_of, "isoformat"):
        data_as_of = data_as_of.isoformat()
    guardian_id = make_guardian_id(class_name, vt_symbol, setting)
    now = datetime.now(CHINA_TZ).isoformat()
    record = {
        "guardian_id": guardian_id,
        "class_name": class_name,
        "vt_symbol": vt_symbol,
        "interval": interval.value,
        "setting": dict(setting),
        "cost": dict(cost),
        "metric": metric,
        "status": "healthy",
        "consecutive_watch": 0,
        "baseline": {
            "kind": "adopt",
            "start": baseline_start.isoformat(),
            "end": baseline_end.isoformat(),
            "data_as_of": data_as_of,
            "score": float(stats.get(metric, 0.0)),
            "stats": stats,
            "created_at": now,
        },
        "checks": [],
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        all_data = _load_all()
        # 保留已有复验历史（同 id 重新建档视为更新基线）
        old = all_data.get(guardian_id)
        if old:
            record["checks"] = old.get("checks", [])[-MAX_CHECKS:]
            record["created_at"] = old.get("created_at", now)
        all_data[guardian_id] = record
        _save_all(all_data)
    return record


def drop(guardian_id: str) -> bool:
    """删除守护档案（退役不删档，手动 drop 才删）。"""
    with _lock:
        all_data = _load_all()
        if guardian_id not in all_data:
            return False
        del all_data[guardian_id]
        _save_all(all_data)
    return True


def _split_new_data_bars(bars: list, k_max: int = 4) -> list[tuple[datetime, datetime]]:
    """把新数据窗口按 bar 数等分为 2~4 段，返回各段 (start_dt, end_dt)。"""
    n = len(bars)
    if n < MIN_NEW_BARS:
        return []
    k = 2 if n < 60 else (3 if n < 120 else min(k_max, 4))
    size = n // k
    bounds: list[tuple[datetime, datetime]] = []
    for i in range(k):
        start_i = i * size
        end_i = (i + 1) * size if i < k - 1 else n
        seg_bars = bars[start_i:end_i]
        bounds.append((seg_bars[0].datetime, seg_bars[-1].datetime))
    return bounds


def recheck(
    main_engine: "MainEngine",
    guardian_id: str,
    progress=None,
) -> dict:
    """对单个守护档案做一次复验，返回复验记录。"""
    with _lock:
        record = _load_all().get(guardian_id)
    if record is None:
        raise ValueError(f"守护档案不存在: {guardian_id}")
    if record["status"] == "retired":
        return {"skipped": True, "reason": "已退役，不再复验（drop 后可重新建档）"}

    baseline = record["baseline"]
    baseline_score = float(baseline.get("score", 0.0))
    data_as_of = baseline.get("data_as_of")
    if isinstance(data_as_of, str):
        try:
            data_as_of = datetime.fromisoformat(data_as_of)
        except ValueError:
            data_as_of = None
    if data_as_of is None:
        data_as_of = datetime.fromisoformat(baseline["end"])

    data_engine = main_engine.get_engine("data")
    if data_engine is None:
        raise RuntimeError("DataEngine not available")
    interval = Interval(record["interval"])
    strategy_engine = main_engine.get_engine("strategy")
    strategy_class = (
        strategy_engine.classes.get(record["class_name"])
        if strategy_engine is not None
        else None
    )
    if strategy_class is None:
        # 策略类不可用：数据/环境问题最多降 watch，不退役
        checked_at = datetime.now(CHINA_TZ).isoformat()
        check = {
            "checked_at": checked_at,
            "error": f"策略类不存在: {record['class_name']}",
            "status_after": "watch",
            "reasons": ["策略类不可用"],
        }
        with _lock:
            all_data = _load_all()
            rec = all_data.get(guardian_id) or record
            rec["checks"] = (rec.get("checks", []) + [check])[-MAX_CHECKS:]
            rec["status"] = "watch"
            rec["consecutive_watch"] = rec.get("consecutive_watch", 0) + 1
            rec["updated_at"] = checked_at
            all_data[guardian_id] = rec
            _save_all(all_data)
        return check

    symbol, exchange = extract_vt_symbol(record["vt_symbol"])
    # 基线之后的新数据（严格 > data_as_of 当日，防与基线窗口重叠）
    new_bars = [
        b for b in data_engine.load_bars(symbol, exchange, interval, data_as_of)
        if b.datetime is not None and b.datetime.date() > data_as_of.date()
    ]
    segments = _split_new_data_bars(new_bars)
    checked_at = datetime.now(CHINA_TZ).isoformat()

    check: dict[str, Any] = {
        "checked_at": checked_at,
        "new_bars": len(new_bars),
        "new_data_start": new_bars[0].datetime.isoformat() if new_bars else None,
        "new_data_end": new_bars[-1].datetime.isoformat() if new_bars else None,
        "k_segments": len(segments),
        "segment_scores": [],
        "segment_detail": [],
    }
    reasons: list[str] = []

    if len(new_bars) < MIN_NEW_BARS:
        return {
            "skipped": True,
            "reason": f"基线后新数据仅 {len(new_bars)} 根（需 ≥{MIN_NEW_BARS}），跳过复验",
        }
    else:
        seg_scores = []
        errors: list[str] = []
        trades_total = 0
        for i, (seg_start, seg_end) in enumerate(segments, 1):
            try:
                stats = run_once(
                    main_engine, strategy_class, record["vt_symbol"], interval,
                    Segment(seg_start, seg_end), record["setting"], record["cost"],
                )
                score = float(stats.get(record["metric"], 0.0) or 0.0)
                seg_scores.append(score)
                trades_total += int(stats.get("total_trade_count", 0) or 0)
                check["segment_detail"].append(
                    {
                        "start": seg_start.isoformat(),
                        "end": seg_end.isoformat(),
                        "score": score,
                        "trades": stats.get("total_trade_count"),
                        "total_return": stats.get("total_return"),
                        "max_ddpercent": stats.get("max_ddpercent"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 —— 单段失败记为缺失
                seg_scores.append(float("-inf"))
                errors.append(f"段{i}: {type(exc).__name__}: {exc}")
            if progress:
                progress(f"守护复验 {i}/{len(segments)}")
        check["segment_scores"] = [
            s for s in seg_scores if s != float("-inf")
        ]
        if errors:
            check["errors"] = errors

        valid_scores = check["segment_scores"]
        if not valid_scores:
            check["error"] = "所有复验段均失败"
            new_status = "watch"
            reasons.append("复验全部失败——按数据残缺处理，最多降观察")
        else:
            robust = 0.6 * _stats.median(valid_scores) + 0.4 * min(valid_scores)
            check["robust_score"] = robust
            check["trade_count"] = trades_total

            if trades_total == 0:
                new_status = "retired"
                reasons.append("交易数枯竭（新窗口 0 笔成交），策略失效")
            elif baseline_score <= 0:
                # 基线为负/零时方向感知退化无意义，只看绝对底线
                if robust > 0:
                    new_status = "healthy"
                    reasons.append(f"基线 {baseline_score:.3f} ≤0，复验 {robust:.3f} >0，视为恢复")
                else:
                    new_status = "watch"
                    reasons.append(f"基线 {baseline_score:.3f} ≤0 且复验 {robust:.3f} 仍 ≤0，维持观察")
            else:
                pct = (baseline_score - robust) / abs(baseline_score)
                check["pct_degradation"] = round(pct, 4)
                if pct > RETIRE_PCT:
                    new_status = "retired"
                    reasons.append(
                        f"相对退化 {pct:.0%} 超过退役线 {RETIRE_PCT:.0%}"
                    )
                elif pct > WATCH_PCT:
                    new_status = "watch"
                    reasons.append(
                        f"相对退化 {pct:.0%} 超过观察线 {WATCH_PCT:.0%}"
                    )
                else:
                    new_status = "healthy"
                    reasons.append(f"相对退化 {pct:.0%}，在可接受范围内")

    # 连续观察计数
    if new_status == "watch":
        consecutive = record.get("consecutive_watch", 0) + 1
        if consecutive >= 2:
            reasons.append(f"已连续 {consecutive} 次观察，建议人工介入")
    elif new_status == "healthy":
        consecutive = 0
    else:
        consecutive = record.get("consecutive_watch", 0)
    check["status_after"] = new_status
    check["reasons"] = reasons

    with _lock:
        all_data = _load_all()
        record = all_data.get(guardian_id) or record
        record["checks"] = (record.get("checks", []) + [check])[-MAX_CHECKS:]
        record["status"] = new_status
        record["consecutive_watch"] = consecutive
        record["updated_at"] = checked_at
        all_data[guardian_id] = record
        _save_all(all_data)

    # 状态恶化时推送通知（watch / retired），健康恢复不打扰
    if new_status in ("watch", "retired") and record["status"] == new_status:
        from .notify import dispatch

        dispatch(
            "策略守护告警",
            f"{record['class_name']} @ {record['vt_symbol']} 状态转为 {new_status}\n"
            + "\n".join(reasons),
            event_kind="guardian",
        )
    return check


def recheck_all(main_engine: "MainEngine", progress=None) -> list[dict]:
    """复验所有未退役档案，返回各档案最新状态。"""
    results: list[dict] = []
    for record in list_guardians():
        if record["status"] == "retired":
            continue
        gid = record["guardian_id"]
        try:
            check = recheck(main_engine, gid, progress=progress)
            results.append({"guardian_id": gid, **check})
        except Exception as exc:  # noqa: BLE001 —— 单档案失败不拖垮整体
            results.append(
                {"guardian_id": gid, "error": f"{type(exc).__name__}: {exc}"}
            )
    return results


def extend_guardian_baseline(guardian_id: str, new_end: str) -> dict:
    """手动把基线延展到指定日期（用于人为确认当前表现可接受）。"""
    raise NotImplementedError
