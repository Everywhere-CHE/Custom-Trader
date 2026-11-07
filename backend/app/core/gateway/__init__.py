from .base import BaseGateway
from .fuyao import FuyaoGateway
from .stock import StockGateway

# CtpGateway 依赖可选的 openctp-ctp 包
try:
    from .ctp import CtpGateway
except ImportError:  # pragma: no cover
    CtpGateway = None  # type: ignore[assignment, misc]

__all__ = ["BaseGateway", "CtpGateway", "FuyaoGateway", "StockGateway"]
