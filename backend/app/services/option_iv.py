"""期权 ATM 隐波期限结构（供期权页路由与关注页复用）。

仅消费本地缓存行情（不做网络回填）：对每个到期日取行权价最接近
标的最新收盘的合约，用其最近收盘价反解 IV。
"""

from __future__ import annotations

from datetime import datetime

from ..core.constant import Exchange, Interval
from .option_pricing import implied_vol
from .option_sync import EXCHANGE_BY_CODE


def atm_iv_term(data_engine, underlying_id: str) -> list[dict]:  # noqa: ANN001
    """返回 [{expiry, call_iv, put_iv, call_strike?, ...}]（升序）。"""
    from sqlalchemy import select

    from ..db.models import OptionContractModel, OptionVarietyModel
    from ..db.session import create_session

    vid = underlying_id if not underlying_id.isdigit() else underlying_id + "O"
    session = create_session()
    try:
        vrow = (
            session.query(OptionVarietyModel)
            .filter(OptionVarietyModel.variety_code == vid)
            .first()
        )
        if vrow is None:
            return []
        contracts = (
            session.query(OptionContractModel)
            .filter(
                OptionContractModel.variety_code == vid,
                OptionContractModel.exchange_code == vrow.exchange_code,
                OptionContractModel.option_type.isnot(None),
            )
            .all()
        )
    finally:
        session.close()

    exchange = EXCHANGE_BY_CODE.get(vrow.exchange_code, Exchange.SSE)
    now = datetime.now()
    # 标的收盘：本地读取（无则空）
    if underlying_id.isdigit():
        ul_symbol = underlying_id + (
            ".SZ" if vrow.exchange_code == "SZSE" else ".SH"
        )
        tail = data_engine.load_bars_tail(
            ul_symbol, exchange, Interval.DAILY, limit=1
        )
    else:
        from .option_sync import CFFEX_DEFAULTS, THS_INDEX_MAP

        session = create_session()
        try:
            urow = (
                session.query(OptionContractModel.underlying_code)
                .filter(
                    OptionContractModel.variety_code == vid,
                    OptionContractModel.exchange_code == vrow.exchange_code,
                )
                .first()
            )
        finally:
            session.close()
        ths = None
        if urow and urow[0]:
            ths = THS_INDEX_MAP.get(urow[0])
        ths = ths or CFFEX_DEFAULTS.get(underlying_id, {}).get("underlying_ths")
        if not ths:
            return []
        tail = data_engine.load_bars_tail(
            ths, Exchange.SSE, Interval.DAILY, limit=1
        )
    u_close = float(tail[-1].close_price or 0) if tail else 0.0
    if u_close <= 0:
        return []

    by_expiry: dict[str, list] = {}
    today = now.strftime("%Y-%m-%d")
    for c in contracts:
        if c.last_trade_date and c.last_trade_date > today:
            by_expiry.setdefault(c.last_trade_date, []).append(c)

    items: list[dict] = []
    for expiry in sorted(by_expiry):
        t_years = max(
            (datetime.strptime(expiry, "%Y-%m-%d") - now).days, 0
        ) / 365.0
        entry: dict = {"expiry": expiry, "call_iv": None, "put_iv": None}
        for otype in ("call", "put"):
            pool = [c for c in by_expiry[expiry] if c.option_type == otype]
            if not pool:
                continue
            c = min(pool, key=lambda x: abs((x.strike_price or 0) - u_close))
            tail = data_engine.load_bars_tail(
                c.thscode, exchange, Interval.DAILY, limit=1
            )
            if not tail or t_years <= 0:
                continue
            iv = implied_vol(
                float(tail[-1].close_price or 0), u_close,
                c.strike_price or 0.0, t_years, 0.02, otype,
            )
            if iv:
                entry[f"{otype}_iv"] = round(iv, 4)
                entry[f"{otype}_strike"] = c.strike_price
        items.append(entry)
    return items
