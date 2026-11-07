"""通用工具函数和常量。"""

from datetime import datetime, timedelta
from decimal import Decimal
from math import floor, ceil
from zoneinfo import ZoneInfo

from .constant import Exchange, Interval


# 平台时区：中国市场
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def bucket_start(dt: datetime, interval: Interval) -> datetime:
    """将时间对齐到其所属K线周期桶的起点。

    分钟线 -> 秒归零；小时线 -> :00:00；日线 -> 零点；
    周线 -> 周一零点。
    """
    if interval in (Interval.MINUTE5, Interval.MINUTE15, Interval.MINUTE30):
        base = dt.replace(second=0, microsecond=0)
        minute_map = {"5m": 5, "15m": 15, "30m": 30}
        step = minute_map.get(interval.value, 1)
        return base.replace(minute=(base.minute // step) * step)
    if interval == Interval.HOUR:
        return dt.replace(minute=0, second=0, microsecond=0)
    if interval == Interval.DAILY:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == Interval.WEEKLY:
        day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    return dt.replace(second=0, microsecond=0)


def extract_vt_symbol(vt_symbol: str) -> tuple[str, Exchange]:
    """将形如 ``IF2509.CFFEX`` 的 vt_symbol 拆分为 (symbol, Exchange)。"""
    symbol, exchange_str = vt_symbol.rsplit(".", 1)
    return symbol, Exchange(exchange_str)


def extract_vt_orderid(vt_orderid: str) -> tuple[str, str]:
    """将形如 ``SIM.1`` 的 vt_orderid 拆分为 (gateway_name, orderid)。"""
    gateway_name, orderid = vt_orderid.split(".", 1)
    return gateway_name, orderid


def round_to(value: float, target: float) -> float:
    """将价格四舍五入到最接近的最小价格变动单位。"""
    d_value: Decimal = Decimal(str(value))
    d_target: Decimal = Decimal(str(target))
    rounded: float = float(int(round(d_value / d_target)) * d_target)
    return rounded


def floor_to(value: float, target: float) -> float:
    """将价格向下取整到最小价格变动单位。"""
    d_value: Decimal = Decimal(str(value))
    d_target: Decimal = Decimal(str(target))
    result: float = float(int(floor(d_value / d_target)) * d_target)
    return result


def ceil_to(value: float, target: float) -> float:
    """将价格向上取整到最小价格变动单位。"""
    d_value: Decimal = Decimal(str(value))
    d_target: Decimal = Decimal(str(target))
    result: float = float(int(ceil(d_value / d_target)) * d_target)
    return result


def now_cn() -> datetime:
    """中国时区的当前时间（naive，去除时区信息）。"""
    return datetime.now(CHINA_TZ).replace(tzinfo=None)


def convert_tz(dt: datetime) -> datetime:
    """将时间转换到中国时区并去除 tzinfo 以便存入数据库。"""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(CHINA_TZ).replace(tzinfo=None)
