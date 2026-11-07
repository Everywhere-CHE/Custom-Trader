"""功能引擎的基类。"""

from abc import ABC
from typing import TYPE_CHECKING

from ..event import EventEngine

if TYPE_CHECKING:
    from .main_engine import MainEngine


class BaseEngine(ABC):
    """挂载到 MainEngine 上的功能引擎抽象基类。"""

    def __init__(
        self,
        main_engine: "MainEngine",
        event_engine: EventEngine,
        engine_name: str,
    ) -> None:
        self.main_engine: MainEngine = main_engine
        self.event_engine: EventEngine = event_engine
        self.engine_name: str = engine_name

    def close(self) -> None:
        """在关闭时释放资源。"""
        return
