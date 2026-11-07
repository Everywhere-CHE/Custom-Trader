import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


# ==============================
# 参数
# ==============================

SYMBOL = "AAPL"

START = "2015-01-01"

END = "2026-01-01"


INITIAL_CAPITAL = 100000


WINDOW = 60


ATR_PERIOD = 14


RISK_PER_TRADE = 0.01



# ==============================
# 获取数据
# ==============================


def get_data(symbol):

    df = yf.download(
        symbol,
        start=START,
        end=END
    )

    df = df[['Open','High','Low','Close','Volume']]

    df.dropna(inplace=True)

    return df



# ==============================
# ATR
# ==============================


def calculate_atr(df, period=14):

    high = df.High
    low = df.Low
    close = df.Close


    tr1 = high-low

    tr2 = abs(high-close.shift())

    tr3 = abs(low-close.shift())


    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)


    atr = tr.rolling(period).mean()


    return atr




# ==============================
# 弧形底识别
# ==============================


def rounded_bottom(df,index):


    if index < WINDOW:
        return False



    data=df.iloc[
        index-WINDOW:index
    ]



    price=data.Close.values


    x=np.arange(WINDOW)



    # 二次拟合

    a,b,c=np.polyfit(
        x,
        price,
        2
    )


    # 开口向上

    if a <=0:
        return False



    # 左侧跌幅

    left_return = (
        price[30]
        /
        price[0]
        -1
    )


    if left_return > -0.1:

        return False



    # 波动收缩

    volatility1=np.std(
        price[:20]
    )


    volatility2=np.std(
        price[20:40]
    )


    if volatility2 > volatility1:

        return False



    # 成交量恢复

    vol_left=data.Volume[:20].mean()

    vol_right=data.Volume[-10:].mean()


    if vol_right < vol_left:

        return False



    # 突破

    breakout = (
        price[-1]
        >
        max(price[:-5])
    )


    if not breakout:

        return False



    return True




# ==============================
# 回测系统
# ==============================


def backtest(df):


    cash=INITIAL_CAPITAL


    position=0


    entry=0


    stop=0


    equity=[]



    for i in range(len(df)):


        price=df.Close.iloc[i]



        # 持仓

        if position>0:


            # ATR止损


            if price < stop:


                cash += (
                    position
                    *
                    price
                )


                position=0



            # 移动止盈

            elif price > entry*1.15:


                stop=max(
                    stop,
                    price*0.9
                )




        # 无仓寻找机会

        if position==0:


            signal=rounded_bottom(
                df,
                i
            )


            if signal:


                atr=df.ATR.iloc[i]


                if np.isnan(atr):

                    continue



                risk_money = (
                    cash
                    *
                    RISK_PER_TRADE
                )


                shares=int(
                    risk_money
                    /
                    (2*atr)
                )


                cost=shares*price



                if cost < cash:


                    position=shares


                    cash-=cost


                    entry=price


                    stop=price-2*atr




        total=cash+position*price


        equity.append(total)




    df=df.iloc[-len(equity):].copy()

    df["Equity"]=equity


    return df





# ==============================
# 运行
# ==============================


if __name__=="__main__":



    df=get_data(SYMBOL)


    df["ATR"]=calculate_atr(
        df,
        ATR_PERIOD
    )


    result=backtest(df)



    print(
        result.tail()
    )



    final=result.Equity.iloc[-1]


    print(
        "初始资金:",
        INITIAL_CAPITAL
    )


    print(
        "最终资金:",
        round(final,2)
    )


    print(
        "收益率:",
        round(
            (final/INITIAL_CAPITAL-1)*100,
            2
        ),
        "%"
    )



    plt.figure(
        figsize=(12,5)
    )


    plt.plot(
        result.index,
        result.Equity
    )


    plt.title(
        "Rounded Bottom Strategy Equity"
    )


    plt.grid()


    plt.show()