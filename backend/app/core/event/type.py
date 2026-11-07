"""事件类型常量。

遵循 vn.py 约定，高频事件类型以 ``.`` 结尾，
以便通过追加 ID 组合出具体主题，例如
``eTick.IF2509.CFFEX``。
"""

EVENT_TICK = "eTick."
EVENT_TRADE = "eTrade."
EVENT_ORDER = "eOrder."
EVENT_POSITION = "ePosition."
EVENT_ACCOUNT = "eAccount."
EVENT_CONTRACT = "eContract."
EVENT_LOG = "eLog"
EVENT_TIMER = "eTimer"
EVENT_GATEWAY_STATUS = "eGatewayStatus"
EVENT_STRATEGY = "eStrategy"
EVENT_STRATEGY_LOG = "eStrategyLog"
