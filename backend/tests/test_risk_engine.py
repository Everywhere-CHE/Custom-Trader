"""RiskEngine 规则测试（直接构造，不经过 Web 栈）。"""

from app.core.constant import Direction, Exchange, Offset, OrderType
from app.core.engine.main_engine import MainEngine
from app.core.engine.oms_engine import OmsEngine
from app.core.engine.risk_engine import RiskEngine
from app.core.event import EVENT_ACCOUNT, Event, EventEngine
from app.core.object import AccountData, OrderRequest, PositionData


def make_engines() -> tuple[MainEngine, RiskEngine, OmsEngine]:
    event_engine = EventEngine(interval=100)
    main_engine = MainEngine(event_engine)
    oms = main_engine.add_engine(OmsEngine)
    risk = main_engine.add_engine(RiskEngine)
    # RiskEngine.__init__ 会从 data/risk_config.json 加载设置（其中
    # active=false）；测试默认需要激活风控，故显式开启。各测试再按需
    # 覆盖具体限值。test_inactive_* 会显式关闭。
    risk.active = True
    return main_engine, risk, oms


def make_req(volume=1.0, offset=Offset.OPEN) -> OrderRequest:
    return OrderRequest(
        symbol="rb2510",
        exchange=Exchange.SHFE,
        direction=Direction.LONG,
        type=OrderType.LIMIT,
        volume=volume,
        price=3000,
        offset=offset,
    )


def test_inactive_passes_everything():
    _, risk, _ = make_engines()
    risk.active = False
    passed, _reason = risk.check(make_req(volume=99999), "SIM")
    assert passed


def test_order_size_limit():
    _, risk, _ = make_engines()
    risk.order_size_limit = 10
    passed, reason = risk.check(make_req(volume=11), "SIM")
    assert not passed
    assert "exceeds limit" in reason

    passed, _ = risk.check(make_req(volume=10), "SIM")
    assert passed


def test_order_flow_limit():
    _, risk, _ = make_engines()
    risk.order_flow_limit = 3
    for _ in range(3):
        passed, _ = risk.check(make_req(), "SIM")
        assert passed
    passed, reason = risk.check(make_req(), "SIM")
    assert not passed
    assert "flow limit" in reason

    # 定时器清空时间窗口
    risk.flow_timer = risk.order_flow_clear
    risk.process_timer_event(Event("eTimer"))
    passed, _ = risk.check(make_req(), "SIM")
    assert passed


def test_daily_order_count_limit():
    _, risk, _ = make_engines()
    risk.order_count_limit = 2
    risk.order_flow_limit = 100
    assert risk.check(make_req(), "SIM")[0]
    assert risk.check(make_req(), "SIM")[0]
    passed, reason = risk.check(make_req(), "SIM")
    assert not passed
    assert "daily order count" in reason


def test_position_limit_on_open():
    _, risk, oms = make_engines()
    risk.position_limit = 5

    # 通过 OMS 事件处理器预置 4 手已有持仓
    position = PositionData(
        gateway_name="SIM",
        symbol="rb2510",
        exchange=Exchange.SHFE,
        direction=Direction.LONG,
        volume=4,
    )
    oms.process_position_event(Event("ePosition.", position))

    passed, reason = risk.check(make_req(volume=2), "SIM")
    assert not passed
    assert "position limit" in reason

    # 平仓不受持仓限制影响
    passed, _ = risk.check(make_req(volume=2, offset=Offset.CLOSE), "SIM")
    assert passed

    # 再加 1 手没问题（4+1 <= 5）
    passed, _ = risk.check(make_req(volume=1), "SIM")
    assert passed


def test_circuit_breaker_blocks_opening():
    _, risk, _ = make_engines()
    risk.daily_loss_limit_pct = 5.0

    # 日初资金余额快照
    account1 = AccountData(gateway_name="SIM", accountid="a", balance=100_000)
    risk.process_account_event(Event(EVENT_ACCOUNT, account1))
    assert not risk.breaker_tripped

    # 6% 回撤触发熔断
    account2 = AccountData(gateway_name="SIM", accountid="a", balance=94_000)
    risk.process_account_event(Event(EVENT_ACCOUNT, account2))
    assert risk.breaker_tripped

    passed, reason = risk.check(make_req(offset=Offset.OPEN), "SIM")
    assert not passed
    assert "circuit breaker" in reason

    # 平仓仍然允许
    passed, _ = risk.check(make_req(offset=Offset.CLOSE), "SIM")
    assert passed

    # 手动重置解除熔断
    risk.reset()
    assert not risk.breaker_tripped


def test_main_engine_send_order_hook():
    """风控检查失败时 MainEngine.send_order 返回空字符串 ''。"""
    main_engine, risk, _ = make_engines()
    risk.order_size_limit = 1

    result = main_engine.send_order(make_req(volume=5), "NOPE")
    assert result == ""
    # 拒单时不消耗计数器
    assert risk.order_count == 0


def test_settings_roundtrip():
    _, risk, _ = make_engines()
    updated = risk.update_settings(
        {"order_size_limit": 42, "unknown_key": 1, "active": False}
    )
    assert updated["order_size_limit"] == 42
    assert updated["active"] is False
    assert "unknown_key" not in updated
