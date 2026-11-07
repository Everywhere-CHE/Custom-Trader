"""风控引擎。

由 ``MainEngine.send_order`` 调用的事前风控检查；所有委托
（手动或策略）在到达网关前都必须通过 ``check()``。规则：

- 单笔委托数量限制；
- 委托流限制（每秒时间窗内的委托数）；
- 每日总委托笔数限制；
- 活动委托数量限制；
- 每日成交笔数限制；
- 单品种持仓限制（开仓时检查）；
- 日内亏损熔断：当账户较当日起始资金回撤超过设定百分比时，
  禁止开新仓，直到下一交易日（或手动重置）。

参数持久化到 ``data/risk_config.json``，重启后自动恢复。
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..constant import Direction, Offset
from ..event import (
    EVENT_ACCOUNT,
    EVENT_TIMER,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from ..object import OrderRequest, TickData
from ..utility import now_cn
from .base import BaseEngine

if TYPE_CHECKING:
    from .main_engine import MainEngine

# 风控参数文件路径
RISK_CONFIG_PATH = Path.cwd() / "data" / "risk_config.json"


class RiskEngine(BaseEngine):
    """事前风控检查。

    每个规则可通过对应的 ``enable_*`` 开关独立控制。
    """

    def __init__(
        self,
        main_engine: "MainEngine",
        event_engine: EventEngine,
    ) -> None:
        super().__init__(main_engine, event_engine, "risk")

        # ----- 参数（可通过 API 在运行时调整） -----
        self.active: bool = True
        self.order_flow_limit: int = 50       # 委托流窗口内的委托数上限
        self.order_flow_clear: int = 1        # 窗口长度（秒）
        self.order_size_limit: float = 100    # 单笔委托最大数量
        self.order_count_limit: int = 2000    # 每日最大委托笔数
        self.active_order_limit: int = 50     # 同时存在的活动委托上限
        self.trade_count_limit: int = 1000    # 每日最大成交笔数
        self.position_limit: float = 200      # 单品种最大持仓
        self.daily_loss_limit_pct: float = 10.0  # 熔断阈值 %

        # ----- 各规则独立开关 -----
        self.enable_order_size: bool = True
        self.enable_order_flow: bool = True
        self.enable_order_count: bool = True
        self.enable_active_order: bool = True
        self.enable_trade_count: bool = True
        self.enable_position_limit: bool = True
        self.enable_daily_loss_breaker: bool = True
        self.enable_trailing_stop: bool = False

        # ----- 每个合约持仓方向的移动止损配置 -----
        # key: "{vt_symbol}.{direction}" (如 "a2609.DCE.LONG")
        # value: 回撤百分比（如 5.0 = 从最高点回撤 5% 时平仓）
        self.trailing_stops: dict[str, float] = {}

        # ----- 运行时状态 -----
        self.order_flow_count: int = 0
        self.flow_timer: int = 0
        self.order_count: int = 0
        self.trade_count: int = 0
        self.day_start_balance: dict[str, float] = {}
        self.breaker_tripped: bool = False
        self.current_date = now_cn().date()
        # 记录每个持仓的最高价（多头）/最低价（空头），用于移动止损判断
        # key: vt_positionid, value: 价格
        self._position_peaks: dict[str, float] = {}

        # 从持久化文件恢复参数
        self._load_settings()

        self.register_event()

    def register_event(self) -> None:
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_ACCOUNT, self.process_account_event)

    # ------------------------------------------------------------------
    # 事件处理器
    # ------------------------------------------------------------------

    def process_timer_event(self, event: Event) -> None:
        self.flow_timer += 1
        if self.flow_timer >= self.order_flow_clear:
            self.flow_timer = 0
            self.order_flow_count = 0

        # 跨日切换
        today = now_cn().date()
        if today != self.current_date:
            self.current_date = today
            self.order_count = 0
            self.trade_count = 0
            self.day_start_balance.clear()
            self._position_peaks.clear()
            if self.breaker_tripped:
                self.breaker_tripped = False
                self.main_engine.write_log(
                    "daily-loss circuit breaker reset (new trading day)",
                    source="RiskEngine",
                )

        # 检查移动止损（每 5 秒检查一次）
        if self.enable_trailing_stop and self.trailing_stops:
            if self.flow_timer % 5 == 0:
                self._check_trailing_stops()

    def process_trade_event(self, event: Event) -> None:
        self.trade_count += 1

    def process_account_event(self, event: Event) -> None:
        account = event.data
        vt_accountid = account.vt_accountid

        if vt_accountid not in self.day_start_balance:
            if account.balance > 0:
                self.day_start_balance[vt_accountid] = account.balance
            return

        start = self.day_start_balance[vt_accountid]
        if start <= 0 or self.breaker_tripped:
            return

        drawdown_pct = (start - account.balance) / start * 100
        if drawdown_pct >= self.daily_loss_limit_pct:
            self.breaker_tripped = True
            self.main_engine.write_log(
                f"daily-loss circuit breaker TRIPPED: account {vt_accountid} "
                f"down {drawdown_pct:.2f}% (limit "
                f"{self.daily_loss_limit_pct}%), opening blocked",
                source="RiskEngine",
                level=logging.CRITICAL,
            )

    # ------------------------------------------------------------------
    # 移动止损
    # ------------------------------------------------------------------

    def _check_trailing_stops(self) -> None:
        """检查所有持仓的移动止损条件，触发时自动平仓。"""
        for pos in self.main_engine.oms.get_all_positions():
            if pos.volume <= 0:
                continue

            key = f"{pos.vt_symbol}.{pos.direction.value}"
            stop_pct = self.trailing_stops.get(key)
            if not stop_pct or stop_pct <= 0:
                continue

            tick = self.main_engine.oms.get_tick(pos.vt_symbol)
            if not tick or not tick.last_price:
                continue

            pid = pos.vt_positionid
            current_price = tick.last_price

            if pos.direction == Direction.LONG:
                # 多头：记录最高价，从最高点回撤时触发
                peak = self._position_peaks.get(pid, current_price)
                if current_price > peak:
                    self._position_peaks[pid] = current_price
                    peak = current_price
                retreat = (peak - current_price) / peak * 100
                if retreat >= stop_pct:
                    self._close_position(pos, f"移动止损触发（多头 from {peak:.2f} 回撤 {retreat:.2f}%）")
            else:
                # 空头：记录最低价，从最低点反弹时触发
                low = self._position_peaks.get(pid, current_price)
                if current_price < low:
                    self._position_peaks[pid] = current_price
                    low = current_price
                retreat = (current_price - low) / low * 100
                if retreat >= stop_pct:
                    self._close_position(pos, f"移动止损触发（空头 from {low:.2f} 反弹 {retreat:.2f}%）")

    def _close_position(self, pos, reason: str) -> None:
        """平掉指定持仓。"""
        try:
            is_long = pos.direction == Direction.LONG
            tick = self.main_engine.oms.get_tick(pos.vt_symbol)
            if not tick or not tick.last_price:
                return
            price = tick.bid_price_1 if is_long else tick.ask_price_1
            if not price:
                price = tick.last_price
            pricetick = 0.01
            contract = self.main_engine.oms.get_contract(pos.vt_symbol)
            if contract and contract.pricetick:
                pricetick = contract.pricetick
            # 限价单：略低于/高于市场价确保成交
            close_price = price - 3 * pricetick if is_long else price + 3 * pricetick
            req = OrderRequest(
                symbol=pos.symbol,
                exchange=pos.exchange,
                direction=Direction.SHORT if is_long else Direction.LONG,
                offset=Offset.CLOSE,
                type="LIMIT",
                volume=pos.volume,
                price=close_price,
                reference="RISK.trailing_stop",
            )
            self.main_engine.send_order(req, pos.gateway_name)
            self.main_engine.write_log(
                f"[{pos.vt_symbol}] {reason}，已提交平仓委托",
                source="RiskEngine",
                level=logging.WARNING,
            )
        except Exception as e:
            self.main_engine.write_log(
                f"[{pos.vt_symbol}] 移动止损平仓失败: {e}",
                source="RiskEngine",
                level=logging.ERROR,
            )

    # ------------------------------------------------------------------
    # 事前检查（由 MainEngine.send_order 调用）
    # ------------------------------------------------------------------

    def check(self, req: OrderRequest, gateway_name: str) -> tuple[bool, str]:
        """返回 (是否通过, 原因)。"""
        if not self.active:
            return True, ""

        if req.volume <= 0:
            return False, "order volume must be positive"

        if self.enable_order_size and req.volume > self.order_size_limit:
            return False, (
                f"order volume {req.volume} exceeds limit "
                f"{self.order_size_limit}"
            )

        if self.enable_order_flow and self.order_flow_count >= self.order_flow_limit:
            return False, (
                f"order flow limit reached "
                f"({self.order_flow_limit}/{self.order_flow_clear}s)"
            )

        if self.enable_order_count and self.order_count >= self.order_count_limit:
            return False, f"daily order count limit {self.order_count_limit} reached"

        if self.enable_trade_count and self.trade_count >= self.trade_count_limit:
            return False, f"daily trade count limit {self.trade_count_limit} reached"

        if self.enable_active_order:
            active_orders = len(self.main_engine.oms.get_all_active_orders())
            if active_orders >= self.active_order_limit:
                return False, f"active order limit {self.active_order_limit} reached"

        is_opening = req.offset in (Offset.NONE, Offset.OPEN)

        if self.enable_daily_loss_breaker and self.breaker_tripped and is_opening:
            return False, "daily-loss circuit breaker tripped: opening blocked"

        if self.enable_position_limit and is_opening:
            # 按方向计算持仓量（多头/空头分开统计）
            held = sum(
                p.volume
                for p in self.main_engine.oms.get_all_positions()
                if p.vt_symbol == req.vt_symbol
                and p.direction == req.direction
            )
            if held + req.volume > self.position_limit:
                return False, (
                    f"position limit: held {held} + new {req.volume} > "
                    f"limit {self.position_limit} for {req.vt_symbol}"
                )

        # 通过：累加计数器
        self.order_flow_count += 1
        self.order_count += 1
        return True, ""

    # ------------------------------------------------------------------
    # 管理 API
    # ------------------------------------------------------------------

    PARAM_NAMES = (
        "active",
        "order_flow_limit",
        "order_flow_clear",
        "order_size_limit",
        "order_count_limit",
        "active_order_limit",
        "trade_count_limit",
        "position_limit",
        "daily_loss_limit_pct",
        "enable_order_size",
        "enable_order_flow",
        "enable_order_count",
        "enable_active_order",
        "enable_trade_count",
        "enable_position_limit",
        "enable_daily_loss_breaker",
        "enable_trailing_stop",
    )

    def get_settings(self) -> dict:
        return {
            **{name: getattr(self, name) for name in self.PARAM_NAMES},
            "trailing_stops": dict(self.trailing_stops),
        }

    def update_settings(self, settings: dict) -> dict:
        for name, value in settings.items():
            if name in self.PARAM_NAMES:
                setattr(self, name, value)
        if "trailing_stops" in settings and isinstance(settings["trailing_stops"], dict):
            self.trailing_stops = {
                k: float(v) for k, v in settings["trailing_stops"].items()
                if v and float(v) > 0
            }
        self._save_settings()
        return self.get_settings()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        """从 data/risk_config.json 加载参数（文件不存在时保持默认值）。"""
        try:
            if RISK_CONFIG_PATH.exists():
                data = json.loads(RISK_CONFIG_PATH.read_text(encoding="utf-8"))
                for name, value in data.items():
                    if name in self.PARAM_NAMES:
                        if isinstance(value, (int, float, bool)):
                            setattr(self, name, value)
                    elif name == "trailing_stops" and isinstance(value, dict):
                        self.trailing_stops = {
                            k: float(v) for k, v in value.items() if float(v) > 0
                        }
                self.main_engine.write_log(
                    f"risk settings loaded from {RISK_CONFIG_PATH}",
                    source="RiskEngine",
                )
        except Exception as e:
            self.main_engine.write_log(
                f"failed to load risk settings: {e}",
                source="RiskEngine",
                level=logging.WARNING,
            )

    def _save_settings(self) -> None:
        """持久化当前参数到 data/risk_config.json。"""
        try:
            RISK_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = self.get_settings()
            RISK_CONFIG_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            self.main_engine.write_log(
                f"failed to save risk settings: {e}",
                source="RiskEngine",
                level=logging.WARNING,
            )

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        return {
            "order_flow_count": self.order_flow_count,
            "order_count": self.order_count,
            "trade_count": self.trade_count,
            "breaker_tripped": self.breaker_tripped,
            "day_start_balance": dict(self.day_start_balance),
            "current_date": self.current_date.isoformat(),
        }

    def reset(self) -> None:
        """手动重置计数器和熔断状态。"""
        self.order_flow_count = 0
        self.order_count = 0
        self.trade_count = 0
        self.breaker_tripped = False
        self.day_start_balance.clear()
        self.main_engine.write_log("risk state manually reset", source="RiskEngine")
