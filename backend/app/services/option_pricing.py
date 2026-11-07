"""期权定价：Black-Scholes 欧式定价 + Greeks + 隐含波动率。

仅依赖标准库 ``math``（正态分布用 ``erf`` 实现），供：

- 回测引擎盯市回退（合约缺日K时按 BSM 估值）；
- T 型报价页的 IV / Greeks 列；
- AI 工具的期权链查询。

约定：标的价 ``s``、行权价 ``k``、剩余期限 ``t`` 以**年**为单位
（交易日口径 ``t = 剩余交易日 / 244`` 或日历口径 / 365，由调用方
决定），``r`` 为年化无风险利率（小数），``v`` 为年化波动率。
"""

from __future__ import annotations

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)

# implied_vol 数值求解参数
IV_MAX_ITER_NEWTON = 50
IV_MAX_ITER_BISECT = 200
IV_LO = 1e-4
IV_HI = 5.0
IV_TOL = 1e-8


def norm_cdf(x: float) -> float:
    """标准正态分布累计函数。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """标准正态分布密度函数。"""
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _d1_d2(s: float, k: float, t: float, r: float, v: float) -> tuple[float, float]:
    """BSM 的 d1 / d2；输入非法（t<=0 / v<=0 / s,k<=0）抛 ValueError。"""
    if s <= 0 or k <= 0:
        raise ValueError("标的价格与行权价必须为正")
    if t <= 0:
        raise ValueError("剩余期限必须为正")
    if v <= 0:
        raise ValueError("波动率必须为正")
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * v * v) * t) / (v * sqrt_t)
    d2 = d1 - v * sqrt_t
    return d1, d2


def bsm_price(
    s: float,
    k: float,
    t: float,
    r: float,
    v: float,
    option_type: str,
) -> float:
    """欧式期权 BSM 价格；已到期（t<=0）返回内在价值。"""
    if t <= 0:
        return max(s - k, 0.0) if option_type == "call" else max(k - s, 0.0)
    d1, d2 = _d1_d2(s, k, t, r, v)
    if option_type == "call":
        return s * norm_cdf(d1) - k * math.exp(-r * t) * norm_cdf(d2)
    return k * math.exp(-r * t) * norm_cdf(-d2) - s * norm_cdf(-d1)


def bsm_greeks(
    s: float,
    k: float,
    t: float,
    r: float,
    v: float,
    option_type: str,
) -> dict[str, float]:
    """BSM Greeks：delta/gamma/theta(每日)/vega(每1%波动率)/rho。

    已到期（t<=0）时 delta 取 0/±1 的阶跃近似，其余为 0。
    """
    if t <= 0:
        if option_type == "call":
            delta = 1.0 if s > k else (0.5 if s == k else 0.0)
        else:
            delta = -1.0 if s < k else (-0.5 if s == k else 0.0)
        return {
            "delta": delta,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
        }
    d1, d2 = _d1_d2(s, k, t, r, v)
    sqrt_t = math.sqrt(t)
    disc = math.exp(-r * t)
    pdf_d1 = norm_pdf(d1)
    gamma = pdf_d1 / (s * v * sqrt_t)
    # theta 换算为每日（年 365 天历法口径）
    if option_type == "call":
        theta = (
            -(s * pdf_d1 * v) / (2 * sqrt_t)
            - r * k * disc * norm_cdf(d2)
        ) / 365.0
        rho = k * t * disc * norm_cdf(d2) / 100.0
        delta = norm_cdf(d1)
    else:
        theta = (
            -(s * pdf_d1 * v) / (2 * sqrt_t)
            + r * k * disc * norm_cdf(-d2)
        ) / 365.0
        rho = -k * t * disc * norm_cdf(-d2) / 100.0
        delta = norm_cdf(d1) - 1.0
    # vega：波动率 +1 个百分点（0.01）的价格变化
    vega = s * sqrt_t * pdf_d1 / 100.0
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "rho": rho,
    }


def implied_vol(
    price: float,
    s: float,
    k: float,
    t: float,
    r: float,
    option_type: str,
) -> float | None:
    """由市场价格反解隐含波动率。

    牛顿迭代（vega 导数）为主，二分法兜底；价格超出无套利边界
    （低于内在价值或高于上界）时返回 ``None``。
    """
    if t <= 0 or s <= 0 or k <= 0 or price <= 0:
        return None
    disc = math.exp(-r * t)
    if option_type == "call":
        intrinsic = max(s - k, 0.0)
        upper = s
    else:
        intrinsic = max(k - s, 0.0)
        upper = k * disc
    if price < intrinsic - 1e-9 or price > upper + 1e-9:
        return None
    if price <= intrinsic + 1e-12:
        return None  # 内在价值平价，IV 无定义（深度实值/零时间价值）

    # 初值：Brenner-Subrahmanyam 近似
    v = max(0.2, (price / s) / 0.3989 / math.sqrt(t))
    for _ in range(IV_MAX_ITER_NEWTON):
        p = bsm_price(s, k, t, r, v, option_type)
        diff = p - price
        if abs(diff) < IV_TOL:
            return v
        d1, _ = _d1_d2(s, k, t, r, v)
        vega = s * math.sqrt(t) * norm_pdf(d1)  # 对 v 的导数（未除100）
        if vega < 1e-12:
            break
        v -= diff / vega
        if v <= IV_LO or v >= IV_HI:
            break

    # 二分兜底
    lo, hi = IV_LO, IV_HI
    for _ in range(IV_MAX_ITER_BISECT):
        mid = 0.5 * (lo + hi)
        p = bsm_price(s, k, t, r, mid, option_type)
        if p > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < IV_TOL:
            break
    v = 0.5 * (lo + hi)
    if v <= IV_LO or v >= IV_HI:
        return None
    return v
