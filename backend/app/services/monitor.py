"""监控中心：规则引擎 + 告警记录 + 自动跟单挂钩。

规则模型（一规则一 JSON 对象，存 ``data/monitor_rules.json``）：

- scope：``symbols``（指定合约）或 ``watchlist``（自选分组动态解析）
- conditions：最多 8 条，字段白名单 + 比较 op，AND/OR 组合
- cooldown：``(rule_id, vt_symbol)`` 键在冷却期内不重复触发（防刷屏核心）
- paper_action：可选自动跟单（触发即向模拟盘下市价单）

告警管道：评估命中 → ``data/alerts.jsonl`` 落盘（滚动 5000 条）
→ 系统通知/飞书/企微（event_kind=strategy）→ 可选模拟盘下单。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import DATA_DIR
from ..core.utility import CHINA_TZ

if TYPE_CHECKING:
    from ..core.engine.main_engine import MainEngine

RULES_FILE = DATA_DIR / "monitor_rules.json"
ALERTS_FILE = DATA_DIR / "alerts.jsonl"
MAX_ALERTS = 5000
MAX_RULES = 50

# 允许用于条件的 tick 字段（白名单，fail-closed）
ALLOWED_FIELDS = (
    "last_price",
    "pct_chg",
    "volume",
    "high",
    "low",
    "open",
    "bid1",
    "ask1",
)
ALLOWED_OPS = (">", ">=", "<", "<=", "==", "!=")

_lock = threading.Lock()
# cooldown: {(rule_id, vt_symbol): monotonic_ts}
_cooldowns: dict[tuple[str, str], float] = {}
# 边缘触发状态机：仅在 False→True 跳变时告警（防规则刚建好瞬间刷屏外的持续满足重复告警，
# 与 cooldown 叠加生效）
_last_match: dict[tuple[str, str], bool] = {}


# ----------------------------------------------------------------------
# 规则存储
# ----------------------------------------------------------------------

def load_rules() -> list[dict]:
    try:
        if RULES_FILE.is_file():
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:  # noqa: BLE001
        pass
    return []


def _save_rules(rules: list[dict]) -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def validate_rule(body: dict) -> dict:
    """校验并规范化规则体（fail-closed）。"""
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("规则名称不能为空")
    scope = body.get("scope") or {}
    scope_type = scope.get("type")
    if scope_type not in ("symbols", "watchlist"):
        raise ValueError("scope.type 必须为 symbols 或 watchlist")
    symbols = [str(s).strip() for s in (scope.get("symbols") or []) if str(s).strip()]
    if scope_type == "symbols" and not symbols:
        raise ValueError("指定合约模式至少需要一个合约")

    conditions = body.get("conditions") or []
    if not conditions or not isinstance(conditions, list):
        raise ValueError("至少需要一个条件")
    if len(conditions) > 8:
        raise ValueError("条件最多 8 条")
    norm_conditions = []
    for cond in conditions:
        field = str(cond.get("field") or "")
        op = str(cond.get("op") or "")
        value = cond.get("value")
        if field not in ALLOWED_FIELDS:
            raise ValueError(f"不支持的字段 {field!r}，可选: {', '.join(ALLOWED_FIELDS)}")
        if op not in ALLOWED_OPS:
            raise ValueError(f"不支持的运算符 {op!r}，可选: {', '.join(ALLOWED_OPS)}")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"条件阈值必须是数字: {value!r}") from None
        norm_conditions.append({"field": field, "op": op, "value": value})

    logic = str(body.get("logic") or "and").lower()
    if logic not in ("and", "or"):
        raise ValueError("logic 必须为 and 或 or")

    try:
        cooldown = max(0, int(body.get("cooldown_seconds") or 300))
    except (TypeError, ValueError):
        cooldown = 300

    paper_action = body.get("paper_action") or {}
    direction = str(paper_action.get("direction") or "").lower()
    if direction not in ("", "buy", "sell"):
        raise ValueError("paper_action.direction 必须为 buy/sell/空")
    try:
        volume = max(0, int(paper_action.get("volume") or 0))
    except (TypeError, ValueError):
        volume = 0
    if direction and volume <= 0:
        raise ValueError("启用自动跟单时 volume 必须大于 0")

    return {
        "name": name,
        "enabled": bool(body.get("enabled", True)),
        "scope": {"type": scope_type, "symbols": symbols},
        "conditions": norm_conditions,
        "logic": logic,
        "cooldown_seconds": cooldown,
        "paper_action": {
            "direction": direction,
            "volume": volume,
        },
    }


def add_rule(body: dict) -> dict:
    rule = validate_rule(body)
    with _lock:
        rules = load_rules()
        if len(rules) >= MAX_RULES:
            raise ValueError(f"规则数已达上限 {MAX_RULES}")
        rule["rule_id"] = f"mr_{int(time.time() * 1000) % 10**10:010d}{len(rules) % 100:02d}"
        rule["created_at"] = datetime.now(CHINA_TZ).isoformat()
        rule["last_triggered_at"] = None
        rules.append(rule)
        _save_rules(rules)
    return rule


def update_rule(rule_id: str, body: dict) -> dict:
    rule = validate_rule(body)
    with _lock:
        rules = load_rules()
        for i, existing in enumerate(rules):
            if existing.get("rule_id") == rule_id:
                rule["rule_id"] = rule_id
                rule["created_at"] = existing.get("created_at")
                rule["last_triggered_at"] = existing.get("last_triggered_at")
                rules[i] = rule
                _save_rules(rules)
                return rule
    raise ValueError(f"规则不存在: {rule_id}")


def delete_rule(rule_id: str) -> bool:
    with _lock:
        rules = load_rules()
        remaining = [r for r in rules if r.get("rule_id") != rule_id]
        if len(remaining) == len(rules):
            return False
        _save_rules(remaining)
    return True


def set_rule_enabled(rule_id: str, enabled: bool) -> dict | None:
    with _lock:
        rules = load_rules()
        for rule in rules:
            if rule.get("rule_id") == rule_id:
                rule["enabled"] = bool(enabled)
                _save_rules(rules)
                return rule
    return None


# ----------------------------------------------------------------------
# 告警记录
# ----------------------------------------------------------------------

def append_alert(alert: dict) -> None:
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with ALERTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")
        # 滚动裁剪：超过 MAX_ALERTS 时重写保留尾部
        try:
            if ALERTS_FILE.stat().st_size > 2 * 1024 * 1024:
                lines = ALERTS_FILE.read_text(encoding="utf-8").splitlines()
                keep = lines[-MAX_ALERTS:]
                ALERTS_FILE.write_text(
                    "\n".join(keep) + ("\n" if keep else ""), encoding="utf-8"
                )
        except OSError:
            pass


def load_alerts(limit: int = 100, rule_id: str = "") -> list[dict]:
    try:
        if not ALERTS_FILE.is_file():
            return []
        lines = ALERTS_FILE.read_text(encoding="utf-8").splitlines()
        alerts = []
        for line in reversed(lines):
            try:
                alert = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rule_id and alert.get("rule_id") != rule_id:
                continue
            alerts.append(alert)
            if len(alerts) >= limit:
                break
        return alerts
    except Exception:  # noqa: BLE001
        return []


def clear_alerts() -> None:
    with _lock:
        if ALERTS_FILE.exists():
            ALERTS_FILE.write_text("", encoding="utf-8")


# ----------------------------------------------------------------------
# 评估引擎
# ----------------------------------------------------------------------

def _tick_field(tick, field: str) -> float | None:
    if field == "pct_chg":
        if tick.last_price and tick.pre_close:
            return (tick.last_price - tick.pre_close) / tick.pre_close * 100
        return None
    value = getattr(tick, field, None)
    if isinstance(value, (int, float)) and value == value:  # NaN 检查
        return float(value)
    return None


def _match_condition(tick, cond: dict) -> bool:
    value = _tick_field(tick, cond["field"])
    if value is None:
        return False
    threshold = cond["value"]
    op = cond["op"]
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    return value != threshold


class MonitorEngine:
    """轻量定时评估器：每 5 秒用最新 tick 快照评估所有启用规则。"""

    EVAL_INTERVAL = 5.0

    def __init__(self, main_engine, event_engine) -> None:
        self.main_engine = main_engine
        self.event_engine = event_engine
        from ..core.event import EVENT_TIMER

        event_engine.register(EVENT_TIMER, self.on_timer)
        self._counter = 0
        main_engine.write_log(
            f"监控引擎已启动（{self.EVAL_INTERVAL:.0f}s 评估周期）", source="Monitor"
        )

    def on_timer(self, event) -> None:
        self._counter += 1
        if self._counter < int(self.EVAL_INTERVAL):
            return
        self._counter = 0
        try:
            self.evaluate_once()
        except Exception:  # noqa: BLE001 —— 评估异常绝不拖垮事件线程
            import logging

            logging.getLogger("custom_trader.monitor").exception("监控评估异常")

    def _resolve_symbols(self, rule: dict) -> list[str]:
        scope = rule.get("scope") or {}
        if scope.get("type") == "watchlist":
            try:
                from ..watchlist import load_watchlist

                return list(load_watchlist())
            except Exception:  # noqa: BLE001
                return []
        return list(scope.get("symbols") or [])

    def evaluate_once(self) -> int:
        """评估一轮，返回本轮触发次数。"""
        oms = self.main_engine.oms
        triggered = 0
        for rule in load_rules():
            if not rule.get("enabled", True):
                continue
            conditions = rule.get("conditions") or []
            logic_all = (rule.get("logic") or "and").lower() == "and"
            for vt_symbol in self._resolve_symbols(rule):
                tick = oms.get_tick(vt_symbol)
                if tick is None or not tick.last_price:
                    continue
                if logic_all:
                    matched = all(_match_condition(tick, c) for c in conditions)
                else:
                    matched = any(_match_condition(tick, c) for c in conditions)

                key = (rule["rule_id"], vt_symbol)
                # 边缘触发：条件持续满足只在跳变时告警
                was_matched = _last_match.get(key, False)
                _last_match[key] = matched
                if not matched or was_matched:
                    continue

                # cooldown 防刷屏
                now = time.monotonic()
                last = _cooldowns.get(key, 0.0)
                cooldown = float(rule.get("cooldown_seconds") or 300)
                if now - last < cooldown:
                    continue
                _cooldowns[key] = now

                self._fire(rule, vt_symbol, tick)
                triggered += 1
        return triggered

    def _fire(self, rule: dict, vt_symbol: str, tick) -> None:
        """命中处理：告警落盘 + 通知推送 + 可选模拟盘跟单。"""
        from .notify import dispatch

        now = datetime.now(CHINA_TZ)
        conditions_text = " 且 ".join(
            f"{c['field']} {c['op']} {c['value']}" for c in rule.get("conditions", [])
        )
        message = (
            f"{rule['name']}：{vt_symbol} 触发 [{conditions_text}]，"
            f"最新 {tick.last_price}"
        )
        append_alert(
            {
                "time": now.isoformat(),
                "rule_id": rule["rule_id"],
                "rule_name": rule["name"],
                "vt_symbol": vt_symbol,
                "message": message,
                "last_price": tick.last_price,
            }
        )
        with _lock:
            rules = load_rules()
            for r in rules:
                if r.get("rule_id") == rule["rule_id"]:
                    r["last_triggered_at"] = now.isoformat()
                    _save_rules(rules)
                    break

        dispatch("监控告警", message, event_kind="strategy")
        self.main_engine.write_log(message, source="Monitor")

        # 可选自动跟单（模拟盘）
        action = rule.get("paper_action") or {}
        if action.get("direction") and action.get("volume"):
            try:
                from .paper import market_order

                result = market_order(
                    self.main_engine,
                    vt_symbol,
                    action["direction"],
                    int(action["volume"]),
                    reason=f"monitor:{rule['name']}",
                )
                append_alert(
                    {
                        "time": now.isoformat(),
                        "rule_id": rule["rule_id"],
                        "rule_name": rule["name"],
                        "vt_symbol": vt_symbol,
                        "message": f"自动跟单: {result.get('message', '')}",
                        "last_price": tick.last_price,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.main_engine.write_log(
                    f"自动跟单失败 {vt_symbol}: {exc}", source="Monitor"
                )

    def close(self) -> None:
        pass
