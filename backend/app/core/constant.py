"""交易平台通用枚举定义。

参考 vn.py ``vnpy/trader/constant.py``，但采用英文枚举值
以便于 JSON 序列化。
"""

from enum import Enum


class Direction(Enum):
    """委托 / 成交 / 持仓的方向。"""

    LONG = "LONG"
    SHORT = "SHORT"
    NET = "NET"


class Offset(Enum):
    """委托 / 成交的开平方向。"""

    NONE = "NONE"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    CLOSETODAY = "CLOSETODAY"
    CLOSEYESTERDAY = "CLOSEYESTERDAY"


class Status(Enum):
    """委托状态。"""

    SUBMITTING = "SUBMITTING"
    NOTTRADED = "NOTTRADED"
    PARTTRADED = "PARTTRADED"
    ALLTRADED = "ALLTRADED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


ACTIVE_STATUSES: set[Status] = {
    Status.SUBMITTING,
    Status.NOTTRADED,
    Status.PARTTRADED,
}


class Product(Enum):
    """产品类别。"""

    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTION = "OPTION"
    INDEX = "INDEX"
    ETF = "ETF"
    SPOT = "SPOT"
    FUND = "FUND"
    BOND = "BOND"


class OrderType(Enum):
    """委托类型。"""

    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"
    FAK = "FAK"
    FOK = "FOK"


class Exchange(Enum):
    """交易所。"""

    # 中国期货
    CFFEX = "CFFEX"  # 中国金融期货交易所
    SHFE = "SHFE"    # 上海期货交易所
    CZCE = "CZCE"    # 郑州商品交易所
    DCE = "DCE"      # 大连商品交易所
    INE = "INE"      # 上海国际能源交易中心
    GFEX = "GFEX"    # 广州期货交易所

    # 中国股票
    SSE = "SSE"      # 上海证券交易所
    SZSE = "SZSE"    # 深圳证券交易所
    BSE = "BSE"      # 北京证券交易所

    # 港股 / 美股（第二阶段及以后）
    SEHK = "SEHK"    # 香港交易所
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"

    # 特殊
    LOCAL = "LOCAL"  # 用于本地生成的数据（模拟 / 回测）


class Interval(Enum):
    """K线周期。"""

    MINUTE = "1m"
    MINUTE5 = "5m"
    MINUTE15 = "15m"
    MINUTE30 = "30m"
    HOUR = "1h"
    DAILY = "d"
    WEEKLY = "w"
    TICK = "tick"


class EngineState(Enum):
    """主引擎的生命周期状态（用于 API 报告）。"""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
