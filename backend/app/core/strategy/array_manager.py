"""带技术指标的时间序列容器。

参考 vn.py 的 ArrayManager 设计，但使用纯 numpy 实现
（计算约定与 talib 兼容），以避免在 Windows 上依赖
ta-lib 原生库。
"""

import numpy as np

from ..object import BarData


class ArrayManager:
    """
    K线 数据的环形缓冲区，附带指标计算方法。收满 ``size`` 根
    K线 后 ``inited`` 变为 True。
    """

    def __init__(self, size: int = 100) -> None:
        self.count: int = 0
        self.size: int = size
        self.inited: bool = False

        self.open_array: np.ndarray = np.zeros(size)
        self.high_array: np.ndarray = np.zeros(size)
        self.low_array: np.ndarray = np.zeros(size)
        self.close_array: np.ndarray = np.zeros(size)
        self.volume_array: np.ndarray = np.zeros(size)
        self.turnover_array: np.ndarray = np.zeros(size)
        self.open_interest_array: np.ndarray = np.zeros(size)

    def update_bar(self, bar: BarData) -> None:
        """把新的一根 K线 推入缓冲区。"""
        self.count += 1
        if not self.inited and self.count >= self.size:
            self.inited = True

        self.open_array[:-1] = self.open_array[1:]
        self.high_array[:-1] = self.high_array[1:]
        self.low_array[:-1] = self.low_array[1:]
        self.close_array[:-1] = self.close_array[1:]
        self.volume_array[:-1] = self.volume_array[1:]
        self.turnover_array[:-1] = self.turnover_array[1:]
        self.open_interest_array[:-1] = self.open_interest_array[1:]

        self.open_array[-1] = bar.open_price
        self.high_array[-1] = bar.high_price
        self.low_array[-1] = bar.low_price
        self.close_array[-1] = bar.close_price
        self.volume_array[-1] = bar.volume
        self.turnover_array[-1] = bar.turnover
        self.open_interest_array[-1] = bar.open_interest

    # ------------------------------------------------------------------
    # 数组访问器
    # ------------------------------------------------------------------

    @property
    def open(self) -> np.ndarray:
        return self.open_array

    @property
    def high(self) -> np.ndarray:
        return self.high_array

    @property
    def low(self) -> np.ndarray:
        return self.low_array

    @property
    def close(self) -> np.ndarray:
        return self.close_array

    @property
    def volume(self) -> np.ndarray:
        return self.volume_array

    @property
    def turnover(self) -> np.ndarray:
        return self.turnover_array

    @property
    def open_interest(self) -> np.ndarray:
        return self.open_interest_array

    # ------------------------------------------------------------------
    # 内部滚动计算辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _rolling_mean(arr: np.ndarray, n: int) -> np.ndarray:
        """滚动均值，前 n-1 个位置为 NaN。"""
        result = np.full_like(arr, np.nan, dtype=float)
        if n <= len(arr):
            windows = np.lib.stride_tricks.sliding_window_view(arr, n)
            result[n - 1:] = windows.mean(axis=1)
        return result

    @staticmethod
    def _rolling_std(arr: np.ndarray, n: int) -> np.ndarray:
        """滚动总体标准差（ddof=0，与 talib 约定一致）。"""
        result = np.full_like(arr, np.nan, dtype=float)
        if n <= len(arr):
            windows = np.lib.stride_tricks.sliding_window_view(arr, n)
            result[n - 1:] = windows.std(axis=1)
        return result

    @staticmethod
    def _ema(arr: np.ndarray, n: int) -> np.ndarray:
        """EMA，以前 n 个值的 SMA 作为初始值（talib 约定）。"""
        result = np.full_like(arr, np.nan, dtype=float)
        if n > len(arr):
            return result
        alpha = 2.0 / (n + 1)
        result[n - 1] = arr[:n].mean()
        for i in range(n, len(arr)):
            result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
        return result

    @staticmethod
    def _wilder(arr: np.ndarray, n: int) -> np.ndarray:
        """Wilder 平滑：以 SMA 作为初始值，之后按 (前值*(n-1)+当前值)/n 递推。"""
        result = np.full_like(arr, np.nan, dtype=float)
        if n > len(arr):
            return result
        result[n - 1] = arr[:n].mean()
        for i in range(n, len(arr)):
            result[i] = (result[i - 1] * (n - 1) + arr[i]) / n
        return result

    # ------------------------------------------------------------------
    # 技术指标
    # ------------------------------------------------------------------

    def sma(self, n: int, array: bool = False) -> float | np.ndarray:
        """简单移动均线。"""
        result = self._rolling_mean(self.close, n)
        if array:
            return result
        return float(result[-1])

    def ema(self, n: int, array: bool = False) -> float | np.ndarray:
        """指数移动均线。"""
        result = self._ema(self.close, n)
        if array:
            return result
        return float(result[-1])

    def std(self, n: int, nbdev: int = 1, array: bool = False) -> float | np.ndarray:
        """滚动标准差（总体）。"""
        result = self._rolling_std(self.close, n) * nbdev
        if array:
            return result
        return float(result[-1])

    def rsi(self, n: int, array: bool = False) -> float | np.ndarray:
        """相对强弱指标 RSI（Wilder 平滑）。"""
        close = self.close
        diff = np.diff(close)
        gains = np.where(diff > 0, diff, 0.0)
        losses = np.where(diff < 0, -diff, 0.0)

        avg_gain = self._wilder(gains, n)
        avg_loss = self._wilder(losses, n)

        result = np.full_like(close, np.nan, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = avg_gain / avg_loss
            rsi = 100.0 - 100.0 / (1.0 + rs)
        rsi = np.where(
            np.isnan(avg_loss), np.nan, np.where(avg_loss == 0, 100.0, rsi)
        )
        result[1:] = rsi
        if array:
            return result
        return float(result[-1])

    def macd(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        array: bool = False,
    ) -> tuple:
        """MACD 线、信号线、柱状图。"""
        ema_fast = self._ema(self.close, fast_period)
        ema_slow = self._ema(self.close, slow_period)
        macd_line = ema_fast - ema_slow

        # 信号线：对 MACD 线的有效部分再做 EMA
        signal = np.full_like(macd_line, np.nan)
        valid_start = slow_period - 1
        valid = macd_line[valid_start:]
        if len(valid) >= signal_period:
            signal[valid_start:] = self._ema(valid, signal_period)
        hist = macd_line - signal

        if array:
            return macd_line, signal, hist
        return float(macd_line[-1]), float(signal[-1]), float(hist[-1])

    def boll(
        self, n: int, dev: float, array: bool = False
    ) -> tuple:
        """布林带：（上轨，下轨）。"""
        mid = self._rolling_mean(self.close, n)
        band = self._rolling_std(self.close, n) * dev
        up = mid + band
        down = mid - band
        if array:
            return up, down
        return float(up[-1]), float(down[-1])

    def atr(self, n: int, array: bool = False) -> float | np.ndarray:
        """平均真实波幅 ATR（Wilder 平滑）。"""
        high, low, close = self.high, self.low, self.close
        prev_close = np.roll(close, 1)

        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close), np.abs(low - prev_close)
            ),
        )
        # 第一个 TR 没有前收盘价：用最高价减最低价
        tr[0] = high[0] - low[0]

        result = self._wilder(tr, n)
        if array:
            return result
        return float(result[-1])

    def donchian(self, n: int, array: bool = False) -> tuple:
        """唐奇安通道：（最高价滚动最大值，最低价滚动最小值）。"""
        up = np.full_like(self.high, np.nan, dtype=float)
        down = np.full_like(self.low, np.nan, dtype=float)
        if n <= len(self.high):
            up[n - 1:] = np.lib.stride_tricks.sliding_window_view(
                self.high, n
            ).max(axis=1)
            down[n - 1:] = np.lib.stride_tricks.sliding_window_view(
                self.low, n
            ).min(axis=1)
        if array:
            return up, down
        return float(up[-1]), float(down[-1])
