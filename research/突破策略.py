import pandas as pd
import numpy as np


class BreakoutFailureShort:


    def __init__(
        self,
        lookback=60,
        breakout_percent=0.02,
        volume_multiple=1.5,
        atr_period=14
    ):

        self.lookback = lookback
        self.breakout_percent = breakout_percent
        self.volume_multiple = volume_multiple
        self.atr_period = atr_period



    # ======================
    # ATR计算
    # ======================

    def atr(self, df):

        high = df["High"]
        low = df["Low"]
        close = df["Close"]


        tr = pd.concat(
            [
                high-low,

                abs(high-close.shift()),

                abs(low-close.shift())

            ],
            axis=1
        ).max(axis=1)


        return tr.rolling(
            self.atr_period
        ).mean()



    # ======================
    # 生成信号
    # ======================

    def generate(self, df):


        df=df.copy()


        df["ATR"]=self.atr(df)


        df["MA20"]=(
            df.Close
            .rolling(20)
            .mean()
        )


        df["Resistance"]=(
            df.High
            .rolling(
                self.lookback
            )
            .max()
            .shift(1)
        )


        df["VolumeMA"]=(
            df.Volume
            .rolling(20)
            .mean()
        )



        # =====================
        # 突破条件
        # =====================


        df["Breakout"]=(

            df.High
            >
            df.Resistance *
            (
                1+self.breakout_percent
            )

        )



        # =====================
        # 成交量确认
        # =====================


        df["VolumeConfirm"]=(

            df.Volume
            >
            df.VolumeMA *
            self.volume_multiple

        )



        # =====================
        # 跌回压力位
        # =====================


        df["Failed"]=(

            df.Close
            <
            df.Resistance

        )



        # =====================
        # 趋势转弱
        # =====================


        df["TrendWeak"]=(

            df.Close
            <
            df.MA20

        )



        # =====================
        # 最终做空信号
        # =====================


        df["SHORT"]=(
            
            df.Breakout
            &
            df.VolumeConfirm
            &
            df.Failed
            &
            df.TrendWeak

        )



        # =====================
        # 交易参数
        # =====================


        df["Entry"]=np.where(
            df.SHORT,
            df.Close,
            np.nan
        )


        # ATR止损

        df["StopLoss"]=np.where(

            df.SHORT,

            df.Close
            +
            2*df.ATR,

            np.nan

        )



        # ATR止盈

        df["TakeProfit"]=np.where(

            df.SHORT,

            df.Close
            -
            6*df.ATR,

            np.nan

        )



        return df