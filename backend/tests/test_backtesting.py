"""BacktestingEngine 回测引擎测试（真实数据，零合成）。

K 线从真实 quant_trader.db 中的 IF2509 1m 库存加载。不产生任何
合成数据；回测统计来自真实历史数据。
"""

import os
import shutil
import tempfile
from datetime import datetime

import pytest

from app.core.constant import Exchange, Interval
from app.core.engine.main_engine import MainEngine
from app.core.object import BarData
from app.core.strategy.backtesting import BacktestingEngine
from app.core.strategy.strategies.ma_cross import MaCrossStrategy
from app.db.data_engine import DataEngine
from app.db.session import init_db


def test_ma_cross_on_real_data():
    """使用真实 IF2509 1m 库存 K 线运行回测，验证引擎能正常完成。"""
    src = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "quant_trader.db")
    )
    if not os.path.exists(src):
        pytest.skip("real database not found: data/quant_trader.db")

    # 复制真实库到临时路径（不污染原库）
    tmp = tempfile.mkdtemp(prefix="qt_bt_")
    tmp_db = os.path.join(tmp, "qt.db")
    shutil.copy(src, tmp_db)
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db}"
    try:
        init_db()
        me = MainEngine()
        data = me.add_engine(DataEngine)
        bars = data.load_bars(
            "IF2509", Exchange.CFFEX, Interval.MINUTE,
            start=datetime(2026, 7, 17), end=datetime(2026, 7, 27),
        )
        if not bars:
            pytest.skip("no IF2509 1m bars in database")

        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbol="IF2509.CFFEX",
            interval=Interval.MINUTE,
            start=datetime(2026, 7, 17),
            end=datetime(2026, 7, 27),
            rate=0.0001, slippage=0, size=10, pricetick=1, capital=1_000_000,
        )
        engine.add_strategy(MaCrossStrategy, {})
        engine.history_data = bars
        engine.run_backtesting()
        stats = engine.calculate_statistics()

        assert stats["total_trade_count"] > 0, "should have at least one trade"
        assert stats["end_balance"] > 0
        assert stats["total_return"] != 0.0
        assert stats["total_days"] >= 1
        assert stats["win_rate"] >= 0.0
    finally:
        os.environ.pop("DATABASE_URL", None)
        if old_url is not None:
            os.environ["DATABASE_URL"] = old_url
        shutil.rmtree(tmp, ignore_errors=True)