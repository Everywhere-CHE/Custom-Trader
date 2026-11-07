"""CTP（THOST_FTDC）常量与平台枚举之间的映射。"""

from openctp_ctp import tdapi

from ...constant import (
    Direction,
    Exchange,
    Offset,
    OrderType,
    Product,
    Status,
)

# ----- 买卖方向 -----
DIRECTION_MAP: dict[Direction, str] = {
    Direction.LONG: tdapi.THOST_FTDC_D_Buy,
    Direction.SHORT: tdapi.THOST_FTDC_D_Sell,
}
DIRECTION_MAP_REVERSE: dict[str, Direction] = {
    v: k for k, v in DIRECTION_MAP.items()
}
# 持仓方向
POS_DIRECTION_MAP: dict[str, Direction] = {
    tdapi.THOST_FTDC_PD_Long: Direction.LONG,
    tdapi.THOST_FTDC_PD_Short: Direction.SHORT,
    tdapi.THOST_FTDC_PD_Net: Direction.NET,
}

# ----- 开平方向 -----
OFFSET_MAP: dict[Offset, str] = {
    Offset.OPEN: tdapi.THOST_FTDC_OF_Open,
    Offset.CLOSE: tdapi.THOST_FTDC_OF_Close,
    Offset.CLOSETODAY: tdapi.THOST_FTDC_OF_CloseToday,
    Offset.CLOSEYESTERDAY: tdapi.THOST_FTDC_OF_CloseYesterday,
}
OFFSET_MAP_REVERSE: dict[str, Offset] = {v: k for k, v in OFFSET_MAP.items()}
OFFSET_MAP_REVERSE[tdapi.THOST_FTDC_OF_ForceClose] = Offset.CLOSE

# ----- 订单状态 -----
STATUS_MAP: dict[str, Status] = {
    tdapi.THOST_FTDC_OST_NoTradeQueueing: Status.NOTTRADED,
    tdapi.THOST_FTDC_OST_NoTradeNotQueueing: Status.NOTTRADED,
    tdapi.THOST_FTDC_OST_PartTradedQueueing: Status.PARTTRADED,
    tdapi.THOST_FTDC_OST_PartTradedNotQueueing: Status.PARTTRADED,
    tdapi.THOST_FTDC_OST_AllTraded: Status.ALLTRADED,
    tdapi.THOST_FTDC_OST_Canceled: Status.CANCELLED,
    tdapi.THOST_FTDC_OST_Unknown: Status.SUBMITTING,
}

# ----- 订单类型：（价格类型、时间条件、数量条件）-----
ORDERTYPE_MAP: dict[OrderType, tuple[str, str, str]] = {
    OrderType.LIMIT: (
        tdapi.THOST_FTDC_OPT_LimitPrice,
        tdapi.THOST_FTDC_TC_GFD,
        tdapi.THOST_FTDC_VC_AV,
    ),
    OrderType.MARKET: (
        tdapi.THOST_FTDC_OPT_AnyPrice,
        tdapi.THOST_FTDC_TC_IOC,
        tdapi.THOST_FTDC_VC_AV,
    ),
    OrderType.FAK: (
        tdapi.THOST_FTDC_OPT_LimitPrice,
        tdapi.THOST_FTDC_TC_IOC,
        tdapi.THOST_FTDC_VC_AV,
    ),
    OrderType.FOK: (
        tdapi.THOST_FTDC_OPT_LimitPrice,
        tdapi.THOST_FTDC_TC_IOC,
        tdapi.THOST_FTDC_VC_CV,
    ),
}
ORDERTYPE_MAP_REVERSE: dict[tuple[str, str, str], OrderType] = {
    v: k for k, v in ORDERTYPE_MAP.items()
}

# ----- 交易所 -----
EXCHANGE_MAP: dict[str, Exchange] = {
    "CFFEX": Exchange.CFFEX,
    "SHFE": Exchange.SHFE,
    "CZCE": Exchange.CZCE,
    "DCE": Exchange.DCE,
    "INE": Exchange.INE,
    "GFEX": Exchange.GFEX,
}

# ----- 产品类型 -----
PRODUCT_MAP: dict[str, Product] = {
    tdapi.THOST_FTDC_PC_Futures: Product.FUTURES,
    tdapi.THOST_FTDC_PC_Options: Product.OPTION,
    tdapi.THOST_FTDC_PC_SpotOption: Product.OPTION,
    tdapi.THOST_FTDC_PC_Combination: Product.SPOT,
}

# ----- 常见期货品种的中文名称（当 SimNow 7x24 环境只返回
# 合约代码而不是完整名称时使用）-----
CN_FUTURE_NAMES: dict[str, str] = {
    # DCE 大商所
    "a": "黄大豆一号",
    "b": "黄大豆二号",
    "bb": "胶合板",
    "c": "玉米",
    "cs": "玉米淀粉",
    "eb": "苯乙烯",
    "eg": "乙二醇",
    "fb": "纤维板",
    "i": "铁矿石",
    "j": "冶金焦炭",
    "jd": "鸡蛋",
    "jm": "焦煤",
    "l": "聚乙烯",
    "lh": "生猪",
    "m": "豆粕",
    "p": "棕榈油",
    "pg": "液化石油气",
    "pp": "聚丙烯",
    "rr": "粳米",
    "v": "聚氯乙烯",
    "y": "豆油",
    "lg": "工业硅",
    # SHFE 上期所
    "ag": "白银",
    "al": "铝",
    "au": "黄金",
    "ao": "氧化铝",
    "br": "丁二烯橡胶",
    "bu": "石油沥青",
    "cu": "铜",
    "fu": "燃料油",
    "hc": "热轧卷板",
    "ni": "镍",
    "pb": "铅",
    "rb": "螺纹钢",
    "ru": "天然橡胶",
    "sn": "锡",
    "sp": "纸浆",
    "ss": "不锈钢",
    "wr": "线材",
    "zn": "锌",
    # CFFEX 中金所
    "IF": "沪深300",
    "IC": "中证500",
    "IH": "上证50",
    "IM": "中证1000",
    "T": "10年期国债",
    "TF": "5年期国债",
    "TS": "2年期国债",
    "TL": "30年期国债",
    # CZCE 郑商所
    "AP": "苹果",
    "CF": "棉花",
    "CJ": "红枣",
    "CY": "棉纱",
    "ER": "早籼稻",
    "FG": "玻璃",
    "JR": "粳稻",
    "LR": "晚籼稻",
    "MA": "甲醇",
    "ME": "甲醇",
    "OI": "菜籽油",
    "PF": "短纤",
    "PK": "花生",
    "PM": "普通小麦",
    "RI": "早籼稻",
    "RM": "菜籽粕",
    "RO": "菜籽油",
    "RS": "油菜籽",
    "SA": "纯碱",
    "SF": "硅铁",
    "SM": "锰硅",
    "SR": "白糖",
    "TA": "精对苯二甲酸",
    "UR": "尿素",
    "WH": "强麦",
    "ZC": "动力煤",
    "ZN": "锌",
    # INE 能源中心
    "bc": "国际铜",
    "lu": "低硫燃料油",
    "nr": "20号胶",
    "sc": "中质含硫原油",
    # GFEX 广期所
    "si": "工业硅",
    "lc": "碳酸锂",
}


def get_cn_name(symbol: str, instrument_name: str) -> str:
    """返回合约的正规中文名称。

    如果 CTP API 返回的是真实名称（而不只是合约代码），则原样使用；
    否则用合约代码前缀在中文名称映射表中查找。
    """
    # 如果名称只是合约代码（例如合约 "a2609" 的名称就是 "a2609"），
    # 则按品种前缀查找
    if instrument_name and instrument_name.lower().startswith(symbol[:2].lower()):
        # 查找最长匹配的前缀
        for prefix_len in range(min(len(symbol), 6), 0, -1):
            prefix = symbol[:prefix_len]
            if prefix in CN_FUTURE_NAMES:
                # 从合约代码中提取年月编码
                digits = symbol[prefix_len:]
                return f"{CN_FUTURE_NAMES[prefix]}{digits}"
    return instrument_name

# CTP 用来表示“无数据”的最大浮点值
MAX_FLOAT = float("1.7976931348623157e+308")


def adjust_price(price: float) -> float:
    """把 CTP 的 DBL_MAX 占位值替换为 0。"""
    if price and price != MAX_FLOAT:
        return price
    return 0.0
