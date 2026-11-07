"""数据健康检查：日线数据完整性检测与一键修复。

覆盖 4 类问题（借鉴 ARL/TSP 的分类，按本平台数据模型裁剪）：

- ``invalid``  OHLC 逻辑错：high < max(o,c,l)、low > min(o,c,l)、收盘 ≤0
- ``missing``  交易日缺口：相邻两根日 K 间隔 >5 个连续工作日
               （无交易日历时以工作日近似，标注 unconfirmed）
- ``stale``    停更：最后一条日 K 距今超过 stale_days（默认 7 天）
- ``sparse``   样本过少：不足 min_bars 根（默认 30）

修复方式：对问题标的调用网关重新下载日 K 覆盖入库
（日线在 FUYAO 已连接时优先走 FUYAO，腾讯兜底）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine


def check_symbol(bars: list, stale_days: int = 7, min_bars: int = 30) -> dict:
    """对单个标的的日 K 列表做健康检查，返回问题清单。"""
    issues: list[dict] = []
    if not bars:
        return {"bars": 0, "first": None, "last": None, "issues": [{"type": "sparse", "detail": "无数据"}]}

    # 1. OHLC 逻辑
    invalid_examples: list[str] = []
    invalid_count = 0
    for b in bars:
        if not b.close_price or not b.high_price or not b.low_price or not b.open_price:
            invalid_count += 1
            if len(invalid_examples) < 3:
                invalid_examples.append(f"{b.datetime:%Y-%m-%d} 空值")
            continue
        if (
            b.high_price < max(b.open_price, b.close_price, b.low_price)
            or b.low_price > min(b.open_price, b.close_price, b.high_price)
            or b.close_price <= 0
        ):
            invalid_count += 1
            if len(invalid_examples) < 3:
                invalid_examples.append(f"{b.datetime:%Y-%m-%d} OHLC 逻辑错")
    if invalid_count:
        issues.append(
            {
                "type": "invalid",
                "count": invalid_count,
                "detail": "；".join(invalid_examples) + ("…" if invalid_count > 3 else ""),
            }
        )

    # 2. 工作日缺口（近似，无日历标注 unconfirmed）
    gap_count = 0
    gap_examples: list[str] = []
    for prev, cur in zip(bars, bars[1:]):
        if prev.datetime is None or cur.datetime is None:
            continue
        workdays = 0
        day = prev.datetime.date() + timedelta(days=1)
        while day < cur.datetime.date() and workdays < 30:
            if day.weekday() < 5:
                workdays += 1
            day += timedelta(days=1)
        if workdays > 5:
            gap_count += 1
            if len(gap_examples) < 3:
                gap_examples.append(f"{prev.datetime:%m-%d}→{cur.datetime:%m-%d}")
    if gap_count:
        issues.append(
            {
                "type": "missing",
                "count": gap_count,
                "detail": "；".join(gap_examples) + "（工作日近似，长假可能误报）",
            }
        )

    # 3. 停更
    last_dt = bars[-1].datetime
    if last_dt is not None:
        age_days = (datetime.now() - last_dt).days
        if age_days > stale_days:
            issues.append(
                {
                    "type": "stale",
                    "count": 1,
                    "detail": f"最后数据 {last_dt:%Y-%m-%d}（{age_days} 天前），已停更",
                }
            )

    # 4. 样本过少
    if len(bars) < min_bars:
        issues.append(
            {
                "type": "sparse",
                "count": 1,
                "detail": f"仅 {len(bars)} 根（< {min_bars}），不足以支撑指标/回测",
            }
        )

    return {
        "bars": len(bars),
        "first": bars[0].datetime.isoformat() if bars[0].datetime else None,
        "last": last_dt.isoformat() if last_dt else None,
        "issues": issues,
    }


def check_all(
    main_engine: "MainEngine",
    symbols: list[str] | None = None,
    bars_per_symbol: int = 800,
) -> dict:
    """检查自选池（或指定标的）的日 K 健康，返回总览。"""
    from ..core.constant import Exchange, Interval
    from ..watchlist import load_watchlist

    data_engine = main_engine.get_engine("data")
    if data_engine is None:
        raise RuntimeError("DataEngine not available")

    if not symbols:
        symbols = list(load_watchlist())
        if not symbols:
            symbols = [t.vt_symbol for t in main_engine.oms.get_all_ticks()]

    results: list[dict] = []
    healthy = 0
    for vt_symbol in symbols[:80]:
        try:
            symbol, exchange = vt_symbol.rsplit(".", 1)
            bars = data_engine.load_bars_tail(
                symbol, Exchange(exchange), Interval.DAILY, bars_per_symbol
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"vt_symbol": vt_symbol, "bars": 0, "first": None, "last": None,
                 "issues": [{"type": "invalid", "count": 1, "detail": f"读取失败: {exc}"}]}
            )
            continue
        report = check_symbol(bars)
        report["vt_symbol"] = vt_symbol
        if report["issues"]:
            results.append(report)
        else:
            healthy += 1

    return {
        "checked": len(symbols[:80]),
        "healthy": healthy,
        "problematic": len(results),
        "problems": results,
        "checked_at": datetime.now().isoformat(),
    }


def repair_symbols(
    main_engine: "MainEngine",
    symbols: list[str],
    days: int = 400,
) -> list[dict]:
    """对指定标的重新下载日 K 入库（FUYAO 优先、网关回退）。"""
    from ..core.constant import Interval
    from ..core.object import HistoryRequest

    results: list[dict] = []
    for vt_symbol in symbols[:30]:
        try:
            symbol, exchange = vt_symbol.rsplit(".", 1)
            contract = main_engine.oms.get_contract(vt_symbol)
            if contract is None or not contract.history_data:
                results.append({"vt_symbol": vt_symbol, "ok": False, "detail": "合约不存在或不支持历史数据"})
                continue
            req = HistoryRequest(
                symbol=symbol,
                exchange=contract.exchange,
                start=datetime.now() - timedelta(days=days),
                end=datetime.now(),
                interval=Interval.DAILY,
            )
            downloaded = main_engine.query_history(req, contract.gateway_name)
            if downloaded:
                data_engine = main_engine.get_engine("data")
                data_engine.save_bars(downloaded)
                results.append(
                    {"vt_symbol": vt_symbol, "ok": True, "detail": f"下载 {len(downloaded)} 根日K"}
                )
            else:
                results.append({"vt_symbol": vt_symbol, "ok": False, "detail": "网关返回空数据"})
        except Exception as exc:  # noqa: BLE001
            results.append({"vt_symbol": vt_symbol, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
    return results
