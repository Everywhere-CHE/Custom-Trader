"""模拟盘（Paper Trading）：虚拟账户 + 快照价撮合 + 台账重放。

账务正确性原则（借鉴 ARL/TSP）：

- ``fills.jsonl`` 成交台账 **append-only 是唯一事实源**，
  持仓/现金/净值全部由台账重放推导；
- 修复一律以追加记录表达（重置 = 归档旧台账另起新账），
  绝不改历史行；
- 价格口径：市价单按最新 tick 快照价成交，按方向加滑点；
- 手续费 = 成交额 × rate；合约乘数从 OMS 合约读取，缺省 1（股票）。

精简边界（P1）：净持仓模型（支持期货多空、股票只做多校验），
不做 T+1 限制与挂单撮合——市价单即时成交。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR
from ..core.constant import Direction, Exchange
from ..core.utility import CHINA_TZ

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine

PAPER_DIR = DATA_DIR / "paper"
SETTINGS_FILE = PAPER_DIR / "settings.json"
FILLS_FILE = PAPER_DIR / "fills.jsonl"

_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "capital": 1_000_000.0,
    "rate": 0.0001,       # 手续费率（按成交额）
    "slippage": 0.0,      # 按价格绝对值（合约用 pricetick 倍数、股票 0.01 等）
}


def load_settings() -> dict:
    config = dict(DEFAULT_SETTINGS)
    try:
        if SETTINGS_FILE.is_file():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key in DEFAULT_SETTINGS:
                if key in data and data[key] is not None:
                    config[key] = data[key]
    except Exception:  # noqa: BLE001
        pass
    return config


def save_settings(updates: dict) -> dict:
    config = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in updates and updates[key] is not None:
            try:
                config[key] = float(updates[key])
            except (TypeError, ValueError):
                continue
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return config


# ----------------------------------------------------------------------
# 台账
# ----------------------------------------------------------------------

def _append_fill(fill: dict) -> None:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with FILLS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fill, ensure_ascii=False) + "\n")


def load_fills() -> list[dict]:
    try:
        if not FILLS_FILE.is_file():
            return []
        fills = []
        for line in FILLS_FILE.read_text(encoding="utf-8").splitlines():
            try:
                fills.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return fills
    except Exception:  # noqa: BLE001
        return []


def _contract_meta(main_engine: "MainEngine", vt_symbol: str) -> tuple[float, float]:
    """(size 合约乘数, pricetick)。取不到时用 (1, 0.01)。"""
    contract = main_engine.oms.get_contract(vt_symbol)
    size = float(getattr(contract, "size", 0) or 0) if contract else 0
    pricetick = float(getattr(contract, "pricetick", 0) or 0) if contract else 0
    return (size or 1.0, pricetick or 0.01)


def _is_stock(vt_symbol: str) -> bool:
    exchange = vt_symbol.rsplit(".", 1)[-1].upper() if "." in vt_symbol else ""
    return exchange in ("SSE", "SZSE", "BSE")


def market_order(
    main_engine: "MainEngine",
    vt_symbol: str,
    direction: str,
    volume: int,
    reason: str = "",
) -> dict:
    """市价单：按最新 tick 快照价即时成交（方向加滑点）。

    direction: "buy" / "sell"。股票校验不允许做空；
    模拟盘为净持仓模型：sell 超过持仓时对股票拒绝、对期货反向开仓。
    """
    direction = direction.lower()
    if direction not in ("buy", "sell"):
        raise ValueError("direction 必须为 buy 或 sell")
    volume = int(volume)
    if volume <= 0:
        raise ValueError("volume 必须大于 0")

    tick = main_engine.oms.get_tick(vt_symbol)
    if tick is None or not tick.last_price:
        raise ValueError(f"无实时行情，无法撮合: {vt_symbol}（请先订阅）")

    settings = load_settings()
    size, pricetick = _contract_meta(main_engine, vt_symbol)
    price = float(tick.last_price)
    if direction == "buy":
        price += settings["slippage"]
    else:
        price -= settings["slippage"]
    if pricetick > 0:
        price = round(price / pricetick) * pricetick
        price = round(price, 6)

    if _is_stock(vt_symbol) and direction == "sell":
        state = replay(main_engine)
        pos = state["positions"].get(vt_symbol)
        held = pos["volume"] if pos and pos["direction"] == "long" else 0
        if volume > held:
            raise ValueError(f"股票不允许做空：当前仅持有 {held} 股/手")

    fee = price * volume * size * settings["rate"]
    now = datetime.now(CHINA_TZ)
    fill = {
        "time": now.isoformat(),
        "vt_symbol": vt_symbol,
        "direction": "LONG" if direction == "buy" else "SHORT",
        "price": price,
        "volume": volume,
        "size": size,
        "fee": round(fee, 2),
        "reason": reason,
        "source": "monitor" if reason.startswith("monitor:") else "manual",
    }
    with _lock:
        _append_fill(fill)
    return {
        "ok": True,
        "message": (
            f"{'买入' if direction == 'buy' else '卖出'} {vt_symbol} "
            f"{volume} @ {price}（手续费 {fee:.2f}）"
        ),
        "fill": fill,
    }


# ----------------------------------------------------------------------
# 台账重放 → 持仓 / 资金
# ----------------------------------------------------------------------

def replay(main_engine: "MainEngine") -> dict:
    """从成交台账重放出持仓与资金（唯一事实源）。"""
    settings = load_settings()
    cash = float(settings["capital"])
    fees_total = 0.0
    realized_total = 0.0
    # 净持仓: vt_symbol -> {direction: 'long'|'short', volume, avg_price}
    positions: dict[str, dict] = {}

    for fill in load_fills():
        vt_symbol = fill["vt_symbol"]
        direction = fill["direction"]
        price = float(fill["price"])
        volume = int(fill["volume"])
        size = float(fill.get("size") or 1)
        fee = float(fill.get("fee") or 0)
        fees_total += fee
        cash -= fee

        pos = positions.get(vt_symbol)
        signed = volume if direction == "LONG" else -volume
        if pos is None:
            positions[vt_symbol] = {
                "direction": "long" if signed > 0 else "short",
                "volume": abs(signed),
                "avg_price": price,
            }
            continue
        current_sign = 1 if pos["direction"] == "long" else -1
        held = pos["volume"] * current_sign
        new_held = held + signed

        # 平仓部分实现盈亏
        if held * signed < 0:  # 方向相反 → 有平仓成分
            closing = min(abs(signed), abs(held))
            pnl_per = (price - pos["avg_price"]) if held > 0 else (pos["avg_price"] - price)
            realized = pnl_per * closing * size
            realized_total += realized
            cash += realized

        if new_held == 0:
            positions.pop(vt_symbol, None)
        elif held == 0:
            # 全新开仓
            positions[vt_symbol] = {
                "direction": "long" if new_held > 0 else "short",
                "volume": abs(new_held),
                "avg_price": price,
            }
        elif signed * held > 0:
            # 同向加仓：均价加权
            total = abs(held) + volume
            avg = (pos["avg_price"] * abs(held) + price * volume) / total
            positions[vt_symbol] = {
                "direction": "long" if new_held > 0 else "short",
                "volume": abs(new_held),
                "avg_price": avg,
            }
        elif abs(signed) <= abs(held):
            # 部分平仓：均价不变
            positions[vt_symbol] = {
                "direction": "long" if new_held > 0 else "short",
                "volume": abs(new_held),
                "avg_price": pos["avg_price"],
            }
        else:
            # 反向穿越：旧仓全平后新开
            positions[vt_symbol] = {
                "direction": "long" if new_held > 0 else "short",
                "volume": abs(new_held),
                "avg_price": price,
            }

    # 未实现盈亏：用最新 tick 估值（拿不到就用持仓均价）
    position_rows = []
    unrealized_total = 0.0
    for vt_symbol, pos in positions.items():
        tick = main_engine.oms.get_tick(vt_symbol)
        mark = float(tick.last_price) if tick and tick.last_price else pos["avg_price"]
        size, _ = _contract_meta(main_engine, vt_symbol)
        direction_sign = 1 if pos["direction"] == "long" else -1
        unrealized = (mark - pos["avg_price"]) * direction_sign * pos["volume"] * size
        unrealized_total += unrealized
        position_rows.append(
            {
                "vt_symbol": vt_symbol,
                "direction": pos["direction"],
                "volume": pos["volume"],
                "avg_price": round(pos["avg_price"], 4),
                "mark_price": mark,
                "unrealized_pnl": round(unrealized, 2),
            }
        )

    equity = cash + unrealized_total
    fills = load_fills()
    return {
        "settings": settings,
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "realized_pnl": round(realized_total, 2),
        "unrealized_pnl": round(unrealized_total, 2),
        "fees_total": round(fees_total, 2),
        "trade_count": len(fills),
        "positions": sorted(position_rows, key=lambda p: p["vt_symbol"]),
        "total_return_pct": round(
            (equity / float(settings["capital"]) - 1) * 100, 2
        ),
    }


def reset() -> dict:
    """重置账户：归档旧台账另起新账（历史绝不删除）。"""
    with _lock:
        if FILLS_FILE.is_file():
            ts = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
            archive = PAPER_DIR / f"fills.{ts}.jsonl"
            FILLS_FILE.replace(archive)
    return {"reset": True}
