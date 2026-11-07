"""日志引擎：将 EVENT_LOG 事件路由到 python logging，
并维护一个内存环形缓冲区供 REST API 使用。"""

import logging
from collections import deque
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from ..event import EVENT_LOG, Event, EventEngine
from ..object import LogData
from .base import BaseEngine

if TYPE_CHECKING:
    from .main_engine import MainEngine


class LogEngine(BaseEngine):
    """处理日志事件。"""

    def __init__(
        self,
        main_engine: "MainEngine",
        event_engine: EventEngine,
    ) -> None:
        super().__init__(main_engine, event_engine, "log")

        # 最近 LogData 的环形缓冲区，供 GET /api/logs 使用
        self.buffer: deque[LogData] = deque(maxlen=1000)

        self.logger: logging.Logger = logging.getLogger("custom_trader")
        self.logger.setLevel(logging.DEBUG)

        if not self.logger.handlers:
            fmt = logging.Formatter(
                "%(asctime)s  %(levelname)-8s %(message)s"
            )

            console = logging.StreamHandler()
            console.setFormatter(fmt)
            console.setLevel(logging.INFO)
            self.logger.addHandler(console)

            log_dir: Path = self._get_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            # 清理 10 天前的旧日志文件（非轮转文件，如 backend.log）
            self._cleanup_old_logs(log_dir)
            # 按天轮转，保留 10 天日志（自动清除超过 10 天的旧文件）
            file_handler = TimedRotatingFileHandler(
                log_dir / "custom_trader.log",
                when="midnight",
                interval=1,
                backupCount=10,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            file_handler.setLevel(logging.DEBUG)
            self.logger.addHandler(file_handler)

        self.event_engine.register(EVENT_LOG, self.process_log_event)

    @staticmethod
    def _get_log_dir() -> Path:
        """日志目录：<project>/data。"""
        return Path(__file__).resolve().parents[4] / "data"

    @staticmethod
    def _cleanup_old_logs(log_dir: Path, days: int = 10) -> None:
        """删除 data/ 下超过 days 天的旧日志文件（非轮转文件）。"""
        import time
        cutoff = time.time() - days * 86400
        for f in log_dir.iterdir():
            if f.is_file() and f.suffix in (".log", ".log.1", ".log.2", ".log.3"):
                if f.stat().st_mtime < cutoff:
                    try:
                        f.unlink()
                    except OSError:
                        pass

    def process_log_event(self, event: Event) -> None:
        """处理日志事件：写入缓冲区并输出到 logging。"""
        log: LogData = event.data
        self.buffer.append(log)
        self.logger.log(log.level, f"[{log.gateway_name}] {log.msg}")

    def get_recent_logs(
        self, limit: int = 100, level: int | None = None
    ) -> list[LogData]:
        """返回最近的日志，最新的排在最前。"""
        logs: list[LogData] = list(self.buffer)
        if level is not None:
            logs = [log for log in logs if log.level >= level]
        return list(reversed(logs[-limit:] if limit else logs))
