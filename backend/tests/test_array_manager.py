"""ArrayManager numpy 指标测试。"""

import numpy as np
import pytest

from app.core.constant import Exchange
from app.core.object import BarData
from app.core.strategy.array_manager import ArrayManager
from datetime import datetime, timedelta


def make_am(closes, highs=None, lows=None, size=None) -> ArrayManager:
    size = size or len(closes)
    am = ArrayManager(size=size)
    base = datetime(2026, 1, 1)
    for i, close in enumerate(closes):
        high = highs[i] if highs else close + 1
        low = lows[i] if lows else close - 1
        am.update_bar(
            BarData(
                gateway_name="T",
                symbol="X",
                exchange=Exchange.LOCAL,
                datetime=base + timedelta(minutes=i),
                open_price=close,
                high_price=high,
                low_price=low,
                close_price=close,
                volume=100,
            )
        )
    return am


def test_inited_flag():
    am = ArrayManager(size=5)
    closes = [1, 2, 3, 4]
    base = datetime(2026, 1, 1)
    for i, c in enumerate(closes):
        am.update_bar(
            BarData(
                gateway_name="T",
                symbol="X",
                exchange=Exchange.LOCAL,
                datetime=base + timedelta(minutes=i),
                close_price=c,
            )
        )
    assert not am.inited
    am.update_bar(
        BarData(
            gateway_name="T",
            symbol="X",
            exchange=Exchange.LOCAL,
            datetime=base + timedelta(minutes=5),
            close_price=5,
        )
    )
    assert am.inited
    assert am.close[-1] == 5


def test_sma():
    closes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    am = make_am(closes)
    # 最后 3 个值的 SMA(3) = (8+9+10)/3
    assert am.sma(3) == pytest.approx(9.0)
    arr = am.sma(3, array=True)
    assert np.isnan(arr[1])
    assert arr[2] == pytest.approx(2.0)  # (1+2+3)/3


def test_std_population():
    closes = [2, 4, 4, 4, 5, 5, 7, 9]
    am = make_am(closes)
    # 整个序列的总体标准差为 2.0
    assert am.std(8) == pytest.approx(2.0)


def test_ema_seed_and_recursion():
    closes = [1, 2, 3, 4, 5]
    am = make_am(closes)
    # EMA(3)：种子值 = mean(1,2,3)=2；alpha=0.5
    # ema[3] = 0.5*4 + 0.5*2 = 3; ema[4] = 0.5*5 + 0.5*3 = 4
    assert am.ema(3) == pytest.approx(4.0)


def test_rsi_all_gains():
    closes = list(range(1, 21))  # 单调递增
    am = make_am(closes)
    assert am.rsi(14) == pytest.approx(100.0)


def test_rsi_hand_computed():
    closes = [10, 11, 10, 12, 11, 13]
    am = make_am(closes)
    # 差分：+1,-1,+2,-1,+2；n=5
    # avg_gain = (1+0+2+0+2)/5 = 1.0 ; avg_loss = (0+1+0+1+0)/5 = 0.4
    # rs = 2.5 ; rsi = 100 - 100/3.5 = 71.4285...
    assert am.rsi(5) == pytest.approx(100 - 100 / 3.5, rel=1e-6)


def test_boll_bands():
    closes = [2, 4, 4, 4, 5, 5, 7, 9]
    am = make_am(closes)
    mid = np.mean(closes)
    std = np.std(closes)
    up, down = am.boll(8, 2)
    assert up == pytest.approx(mid + 2 * std)
    assert down == pytest.approx(mid - 2 * std)


def test_atr_hand_computed():
    # 恒定波幅的 K 线：TR = high-low 恒为 2 -> ATR = 2
    closes = [10] * 10
    highs = [11] * 10
    lows = [9] * 10
    am = make_am(closes, highs, lows)
    assert am.atr(5) == pytest.approx(2.0)


def test_donchian():
    closes = [5, 6, 7, 8, 9]
    highs = [6, 7, 8, 9, 10]
    lows = [4, 5, 6, 7, 8]
    am = make_am(closes, highs, lows)
    up, down = am.donchian(3)
    assert up == pytest.approx(10)   # 最后 3 根最高价的最大值
    assert down == pytest.approx(6)  # 最后 3 根最低价的最小值


def test_macd_structure():
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 1, 60))
    am = make_am(list(closes))
    macd, signal, hist = am.macd(12, 26, 9)
    assert not np.isnan(macd)
    assert not np.isnan(signal)
    assert hist == pytest.approx(macd - signal)
