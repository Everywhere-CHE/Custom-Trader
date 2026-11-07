"""异步回测任务管理器。

提交即返回 ``task_id``，回测在后台 daemon 线程执行；
前端通过 ``GET /backtest/{task_id}`` 轮询状态与结果。
长回测不再受 HTTP 超时限制（取代旧的 ``run_in_threadpool``
请求内阻塞模型）。

设计要点：
- 任务状态机 ``pending → running → done | failed``，进度通过
  ``message`` 字段（如 "回测中 2/6"）暴露；
- 结果与原同步路由**逐字段一致**（statistics/daily_results/trades/logs），
  仅交付方式从"请求内返回"改为"轮询获取"；
- 任务状态存内存（单例），重启即清空——与参照版行为一致，
  后续可持久化到 DB。
- 回测线程通过 ``main_engine`` 取 DataEngine 读历史数据；这与旧
  ``run_in_threadpool`` 同样是跨线程访问 DataEngine，未引入新的线程
  安全问题（SQLAlchemy 会话 ``check_same_thread=False``）。
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from ..core.constant import Interval
from ..core.engine.main_engine import MainEngine
from ..core.strategy.backtesting import BacktestingEngine
from ..core.utility import CHINA_TZ

if TYPE_CHECKING:
    from ..api.schemas import BacktestRequestBody


@dataclass
class BacktestTask:
    """单个回测任务的状态。"""

    task_id: str
    status: str = "pending"  # pending | running | done | failed
    message: str = ""
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result: dict | None = None
    error: str = ""
    kind: str = "backtest"  # backtest | robust | walkforward

    def to_dict(self, include_result: bool = True) -> dict:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "kind": self.kind,
        }
        if include_result:
            d["result"] = self.result
        return d


class BacktestManager:
    """管理后台回测任务：提交、状态查询、历史裁剪。"""

    def __init__(self, max_history: int = 50) -> None:
        self._tasks: dict[str, BacktestTask] = {}
        self._lock = threading.Lock()
        self._max_history = max_history

    def submit(
        self,
        main_engine: MainEngine,
        strategy_classes: list[type],
        vt_symbols: list[str],
        body: "BacktestRequestBody",
        interval: Interval,
    ) -> BacktestTask:
        """提交一个回测任务，立即返回 task_id（后台线程执行）。"""
        task_id = uuid.uuid4().hex[:12]
        now = datetime.now(CHINA_TZ).isoformat()
        task = BacktestTask(task_id=task_id, status="pending", created_at=now)
        with self._lock:
            self._tasks[task_id] = task
            self._trim_locked()
        thread = threading.Thread(
            target=self._run,
            args=(task_id, main_engine, strategy_classes, vt_symbols, body, interval),
            name=f"Backtest-{task_id}",
            daemon=True,
        )
        thread.start()
        return task

    def get(self, task_id: str) -> BacktestTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def submit_generic(
        self,
        runner: "Callable[[Callable[[str], None]], dict]",
        kind: str = "backtest",
        label: str = "",
    ) -> BacktestTask:
        """提交一个自定义重活（优化器/守护复验等），后台线程执行。

        runner 接收一个 ``progress(message)`` 回调，返回结果 dict；
        抛出的任何异常都会把任务标记为 failed。
        """
        task_id = uuid.uuid4().hex[:12]
        now = datetime.now(CHINA_TZ).isoformat()
        task = BacktestTask(
            task_id=task_id, status="pending", created_at=now, kind=kind
        )
        task.message = label
        with self._lock:
            self._tasks[task_id] = task
            self._trim_locked()
        thread = threading.Thread(
            target=self._run_generic,
            args=(task_id, runner),
            name=f"{kind.capitalize()}-{task_id}",
            daemon=True,
        )
        thread.start()
        return task

    def _run_generic(
        self,
        task_id: str,
        runner: "Callable[[Callable[[str], None]], dict]",
    ) -> None:
        def progress(message: str) -> None:
            self._set_status(task_id, "running", message=message)

        try:
            result = runner(progress)
            self._set_status(
                task_id,
                "done",
                finished=True,
                message="完成",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(
                task_id,
                "failed",
                finished=True,
                error=f"{type(exc).__name__}: {exc}",
            )
            traceback.print_exc()

    def list(self) -> list[BacktestTask]:
        with self._lock:
            return list(self._tasks.values())

    def clear(self) -> None:
        """清空所有任务（测试用）。"""
        with self._lock:
            self._tasks.clear()

    # ------------------------------------------------------------------
    # 后台执行（搬自旧 routes/backtest.py 的 _run，逻辑保持一致）
    # ------------------------------------------------------------------
    def _run(
        self,
        task_id: str,
        main_engine: MainEngine,
        strategy_classes: list[type],
        vt_symbols: list[str],
        body: "BacktestRequestBody",
        interval: Interval,
    ) -> None:
        # 延迟导入，避免 services -> api 的导入期依赖
        from ..api.schemas import serialize_trade

        task = self._tasks.get(task_id)
        if task is None:
            return
        self._set_status(task_id, "running", started=True, message="回测启动")

        try:
            data_engine = main_engine.get_engine("data")
            if data_engine is None:
                raise RuntimeError("DataEngine not available")

            total = max(1, len(vt_symbols) * len(strategy_classes))
            done = 0
            all_statistics: list[dict] = []
            all_daily_results: list[dict] = []
            all_trades: list = []
            all_logs: list[str] = []

            for vs in vt_symbols:
                for sc in strategy_classes:
                    engine = BacktestingEngine()
                    engine.set_parameters(
                        vt_symbol=vs,
                        interval=interval,
                        start=body.start,
                        end=body.end,
                        rate=body.rate,
                        slippage=body.slippage,
                        size=body.size,
                        pricetick=body.pricetick,
                        capital=body.capital,
                    )
                    engine.add_strategy(sc, body.setting)
                    engine.load_data(data_engine)
                    if not engine.history_data:
                        done += 1
                        self._set_status(
                            task_id,
                            "running",
                            message=f"回测中 {done}/{total}（{vs} 无数据）",
                        )
                        continue
                    try:
                        engine.run_backtesting()
                    except ValueError:
                        done += 1
                        continue
                    all_statistics.append(
                        {
                            "vt_symbol": vs,
                            "class_name": sc.__name__,
                            **engine.calculate_statistics(),
                        }
                    )
                    all_daily_results.extend(engine.get_daily_results())
                    all_trades.extend(engine.get_all_trades())
                    all_logs.extend(engine.logs)
                    done += 1
                    self._set_status(
                        task_id,
                        "running",
                        message=f"回测中 {done}/{total}",
                    )

            if not all_statistics:
                self._set_status(
                    task_id,
                    "failed",
                    finished=True,
                    error="没有可回测的数据，请先下载数据",
                )
                return

            result = {
                "statistics": all_statistics,
                "daily_results": all_daily_results,
                "trades": [serialize_trade(t) for t in all_trades],
                "logs": all_logs,
            }
            self._set_status(
                task_id,
                "done",
                finished=True,
                message="回测完成",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - 任何异常都标记为 failed 而非崩溃线程
            self._set_status(
                task_id,
                "failed",
                finished=True,
                error=f"{type(exc).__name__}: {exc}",
            )
            traceback.print_exc()

    # ------------------------------------------------------------------
    # 内部：状态更新与裁剪
    # ------------------------------------------------------------------
    def _set_status(
        self,
        task_id: str,
        status: str,
        *,
        started: bool = False,
        finished: bool = False,
        message: str | None = None,
        error: str | None = None,
        result: dict | None = None,
    ) -> None:
        now = datetime.now(CHINA_TZ).isoformat()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = status
            if started:
                task.started_at = now
            if finished:
                task.finished_at = now
            if message is not None:
                task.message = message
            if error is not None:
                task.error = error
            if result is not None:
                task.result = result

    def _trim_locked(self) -> None:
        """保留最近 max_history 个任务，剔除最老的已完成任务。"""
        if len(self._tasks) <= self._max_history:
            return
        finished = [
            (tid, t)
            for tid, t in self._tasks.items()
            if t.status in ("done", "failed")
        ]
        finished.sort(key=lambda x: x[1].finished_at or x[1].created_at or "")
        excess = len(self._tasks) - self._max_history
        for tid, _ in finished[:excess]:
            self._tasks.pop(tid, None)
        if len(self._tasks) > self._max_history:
            all_sorted = sorted(
                self._tasks.items(),
                key=lambda x: x[1].created_at or "",
            )
            for tid, _ in all_sorted[: len(self._tasks) - self._max_history]:
                self._tasks.pop(tid, None)


# 模块级单例：路由直接导入使用（任务状态存内存，重启即清空）
backtest_manager = BacktestManager()
