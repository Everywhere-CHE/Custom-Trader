"""应用服务层。

基础设施服务（与交易引擎分家）：
- :mod:`backtest_manager`：异步回测任务管理（提交返回 task_id，后台线程执行）
- :mod:`data_recorder`：tick/K线落库（从 DataEngine 抽出的持久化职责）

服务层不持有交易状态，仅消费事件 / 执行 IO，便于独立测试与替换。
"""
