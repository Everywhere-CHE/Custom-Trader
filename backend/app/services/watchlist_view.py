"""关注列表视图服务：列表摘要与单标的聚合详情。

股票/期货实时价走 OMS tick（自动订阅保障）；期权不做实时订阅
（端外无期权快照），价格走本地日K尾盘 + 惰性补拉。

关键价位复用 :func:`services.key_levels.compute_key_levels_for`；
期权 IV 复用 :mod:`services.option_pricing`；ATM 期限结构复用
:func:`services.option_iv.atm_iv_term`。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..core.constant import Interval
from ..core.utility import CHINA_TZ
from ..watchlist import load_watchlist_entries

# 异动行匹配用的 key 形态：abnormal 的 vt_symbol = "symbol.EXCHANGE"


def _abnormal_index() -> dict[str, dict]:
    """全市场异动（30 分钟缓存），返回 vt_symbol → 行。"""
    from .abnormal_moves import compute_abnormal

    try:
        data = compute_abnormal()
    except Exception:  # noqa: BLE001
        return {}
    return {
        it.get("vt_symbol") or "": it for it in (data.get("items") or [])
    }


def _prev_daily_close(data_engine, item_id: str, kind: str = "stock") -> float:  # noqa: ANN001
    """日线倒数第二根收盘（作昨收兜底；期权经 thscode 归一）。"""
    bars = _bars_for_item(item_id, kind, data_engine)
    if len(bars) >= 2:
        # 最后一根可能是今天（未收盘），取倒数第二根
        today = datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
        prev = bars[-2]
        if bars[-1].datetime and bars[-1].datetime.strftime("%Y-%m-%d") >= today:
            return float(prev.close_price or 0)
        return float(bars[-1].close_price or 0)
    return 0.0


def _bars_tail(data_engine, symbol: str, exchange, limit: int = 6):  # noqa: ANN001
    try:
        return data_engine.load_bars_tail(symbol, exchange, Interval.DAILY, limit)
    except Exception:  # noqa: BLE001
        return []


def _quote_of(entry: dict, main_engine, data_engine) -> dict:  # noqa: ANN001
    """单条目的行情快照：实时 tick 优先（已订阅的 A股/期货/CTP 期权
    均适用），无 tick 时按日K尾盘兜底（期权限日线口径）。"""
    item_id = entry["id"]
    kind = entry["kind"]
    quote: dict = {"last": None, "prev_close": None, "change_pct": None, "source": None}

    tick = main_engine.oms.get_tick(item_id)
    if tick is not None and float(tick.last_price or 0):
        pre = float(tick.pre_close or 0)
        last = float(tick.last_price or 0)
        change = (last / pre - 1) if last and pre else None
        if change is None and last:
            # openctp 模拟环境等场景 tick 无昨收：用日线前一收兜底
            prev_daily = _prev_daily_close(data_engine, item_id, kind)
            if prev_daily:
                change = last / prev_daily - 1
                pre = pre or prev_daily
        quote.update(
            last=last,
            prev_close=pre or None,
            change_pct=change,
            source="tick",
        )
        return quote

    # 日K尾盘兜底（期权必需；股票/期货未订阅时也尽量给出收盘口径）
    try:
        bars = _bars_for_item(item_id, kind, data_engine)
        if bars:
            last = float(bars[-1].close_price or 0)
            prev = float(bars[-2].close_price or 0) if len(bars) > 1 else 0.0
            quote.update(
                last=last or None,
                prev_close=prev or None,
                change_pct=(last / prev - 1) if last and prev else None,
                source="daily",
                as_of=bars[-1].datetime.strftime("%Y-%m-%d") if bars[-1].datetime else None,
            )
    except Exception:  # noqa: BLE001
        pass
    return quote


def _bare_id(item_id: str) -> str:
    return item_id.rsplit(".", 1)[0] if "." in item_id else item_id


def _bars_for_item(item_id: str, kind: str, data_engine):  # noqa: ANN001
    """按条目类型取本地日K（期权代码做 thscode 归一）。"""
    from ..core.constant import Exchange
    from ..core.utility import extract_vt_symbol
    from .option_sync import _exchange_from_thscode, resolve_option_thscode

    if kind in ("option_underlying",):
        exchange, symbol = _underlying_bar_key(item_id)
        return _bars_tail(data_engine, symbol, exchange)

    if kind == "option_contract":
        # 兼容两种形态：fuyao thscode（含 .CFE/.SH 后缀）与 CTP 代码
        exchange = _exchange_from_thscode(item_id)
        if exchange is Exchange.LOCAL and "." in item_id:
            bare = _bare_id(item_id)
            exch_str = item_id.rsplit(".", 1)[1]
            thscode = resolve_option_thscode(bare, exch_str)
            if thscode:
                return _bars_tail(
                    data_engine, thscode, _exchange_from_thscode(thscode)
                )
        return _bars_tail(data_engine, item_id, exchange)

    try:
        symbol, exchange = extract_vt_symbol(item_id)
    except Exception:  # noqa: BLE001
        return []
    return _bars_tail(data_engine, symbol, exchange)


def _underlying_bar_key(underlying_id: str) -> tuple:  # noqa: ANN001
    """期权标的 → (Exchange, bars.symbol)。"""
    from ..core.constant import Exchange
    from .option_sync import CFFEX_DEFAULTS, THS_INDEX_MAP

    if underlying_id.isdigit():
        from ..db.models import OptionVarietyModel
        from ..db.session import create_session

        vid = underlying_id + "O"
        session = create_session()
        try:
            row = (
                session.query(OptionVarietyModel.exchange_code)
                .filter(OptionVarietyModel.variety_code == vid)
                .first()
            )
        finally:
            session.close()
        suffix = ".SZ" if (row and row[0] == "SZSE") else ".SH"
        exchange = Exchange.SZSE if (row and row[0] == "SZSE") else Exchange.SSE
        return exchange, underlying_id + suffix
    # CFFEX 品种：IO → 000300.SH
    ths = CFFEX_DEFAULTS.get(underlying_id, {}).get("underlying_ths")
    if underlying_code := _detail_underlying_code(underlying_id):
        ths = THS_INDEX_MAP.get(underlying_code) or ths
    return Exchange.SSE, ths or underlying_id


def _detail_underlying_code(underlying_id: str) -> str | None:
    from ..db.models import OptionContractModel, OptionVarietyModel
    from ..db.session import create_session

    vid = underlying_id if not underlying_id.isdigit() else underlying_id + "O"
    session = create_session()
    try:
        row = (
            session.query(OptionContractModel.underlying_code)
            .filter(OptionContractModel.variety_code == vid)
            .first()
        )
    finally:
        session.close()
    return row[0] if row else None


def summary(main_engine) -> dict:  # noqa: ANN001
    """列表页一次性摘要：全部条目的行情 + 异动标记。"""
    data_engine = main_engine.get_engine("data")
    abnormal = _abnormal_index()
    items = []
    for entry in load_watchlist_entries():
        quote = _quote_of(entry, main_engine, data_engine)
        ab = abnormal.get(entry["id"])
        items.append(
            {
                **entry,
                "quote": quote,
                "abnormal": {
                    "level": ab.get("level"),
                    "closeness": ab.get("closeness"),
                    "ret3_pct": ab.get("ret3_pct"),
                }
                if ab
                else None,
            }
        )
    return {"items": items, "as_of": datetime.now(CHINA_TZ).isoformat()}


def detail(main_engine, item_id: str, kind: str | None = None) -> dict:  # noqa: ANN001
    """单标的聚合详情（行情/关键价位/异动/告警/期权摘要）。"""
    from ..watchlist import _infer_kind

    data_engine = main_engine.get_engine("data")
    kind = kind or _infer_kind(item_id)
    entry = {
        "id": item_id,
        "kind": kind,
        "name": next(
            (e["name"] for e in load_watchlist_entries() if e["id"] == item_id),
            item_id,
        ),
    }
    out: dict = {"id": item_id, "kind": kind, "name": entry["name"]}
    out["quote"] = _quote_of(entry, main_engine, data_engine)
    abnormal = _abnormal_index().get(item_id)
    out["abnormal"] = abnormal if kind in ("stock", "future") else None
    out["alerts"] = _recent_alerts(item_id)

    if kind in ("stock", "future"):
        from .key_levels import compute_key_levels_for

        try:
            out["key_levels"] = compute_key_levels_for(main_engine, item_id, 250)
        except Exception as exc:  # noqa: BLE001
            out["key_levels"] = {"error": str(exc)}
        out["closes"] = _recent_closes(main_engine, item_id)
        return out

    # 期权 ------------------------------------------------------------
    if kind == "option_contract":
        out["contract"] = _contract_meta(item_id)
        out["iv_snapshot"] = _option_iv_snapshot(data_engine, item_id)
        return out

    # option_underlying
    out["iv_term"] = _atm_iv_term_safe(data_engine, item_id)
    return out


def _recent_alerts(item_id: str, limit: int = 8) -> list[dict]:
    from .monitor import load_alerts

    try:
        return [
            a
            for a in load_alerts(limit=200)
            if a.get("vt_symbol") == item_id
        ][:limit]
    except Exception:  # noqa: BLE001
        return []


def _recent_closes(main_engine, vt_symbol: str, days: int = 30) -> list[dict]:  # noqa: ANN001
    from ..core.utility import extract_vt_symbol

    data_engine = main_engine.get_engine("data")
    try:
        symbol, exchange = extract_vt_symbol(vt_symbol)
    except Exception:  # noqa: BLE001
        return []
    bars = _bars_tail(data_engine, symbol, exchange, days)
    return [
        {
            "date": b.datetime.strftime("%Y-%m-%d") if b.datetime else None,
            "close": b.close_price,
        }
        for b in bars
    ]


def _contract_meta(thscode: str) -> dict | None:
    from ..db.models import OptionContractModel
    from ..db.session import create_session

    session = create_session()
    try:
        row = (
            session.query(OptionContractModel)
            .filter(OptionContractModel.thscode == thscode)
            .first()
        )
    finally:
        session.close()
    if row is None:
        return None
    return {
        "name": row.name,
        "option_type": row.option_type,
        "strike_price": row.strike_price,
        "last_trade_date": row.last_trade_date,
        "list_date": row.list_date,
        "trade_amount": row.trade_amount,
        "margin_rate": row.margin_rate,
        "underlying_code": row.underlying_code,
        "exercise_style": row.exercise_style,
    }


def _option_iv_snapshot(data_engine, thscode: str) -> dict | None:  # noqa: ANN001
    """期权合约最新收盘 → IV/Delta（标的收盘取本地，缺失为 null）。"""
    from ..db.models import OptionContractModel
    from ..db.session import create_session
    from .option_pricing import bsm_greeks, implied_vol
    from .option_sync import _exchange_from_thscode, ensure_option_bars

    session = create_session()
    try:
        row = (
            session.query(OptionContractModel)
            .filter(OptionContractModel.thscode == thscode)
            .first()
        )
    finally:
        session.close()
    if row is None:
        return None
    exchange = _exchange_from_thscode(thscode)
    variety = row.variety_code
    now = datetime.now()
    start = (now - timedelta(days=14)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    bars = _bars_tail(data_engine, thscode, exchange, 3)
    if not bars:
        try:
            from .option_sync import load_api_key, make_client, make_rate_box

            api_key = load_api_key()
            if api_key:
                client = make_client(api_key)
                rate_box = make_rate_box()
                try:
                    ensure_option_bars(
                        data_engine, thscode, start, now,
                        client=client, rate_box=rate_box,
                    )
                finally:
                    client.close()
                bars = _bars_tail(data_engine, thscode, exchange, 3)
        except Exception:  # noqa: BLE001
            pass
    if not bars:
        return {"price": None, "iv": None, "delta": None, "note": "无本地行情"}
    price = float(bars[-1].close_price or 0)
    out: dict = {
        "price": price or None,
        "as_of": bars[-1].datetime.strftime("%Y-%m-%d") if bars[-1].datetime else None,
        "iv": None,
        "delta": None,
    }
    u_close = _underlying_close_local(
        data_engine, row.underlying_code, variety_hint=variety
    )
    t_years = 0.0
    if row.last_trade_date:
        expiry_dt = datetime.strptime(row.last_trade_date, "%Y-%m-%d")
        t_years = max((expiry_dt - now).days, 0) / 365.0
    if u_close and t_years > 0 and price > 0 and row.strike_price:
        iv = implied_vol(price, u_close, row.strike_price, t_years, 0.02, row.option_type or "call")
        if iv:
            out["iv"] = round(iv, 4)
            g = bsm_greeks(u_close, row.strike_price, t_years, 0.02, iv, row.option_type or "call")
            out["delta"] = round(g["delta"], 4)
        out["underlying_close"] = u_close
        out["days_to_expiry"] = max((expiry_dt - now).days, 0)
    return out


def _underlying_close_local(data_engine, underlying_code, variety_hint=None) -> float:  # noqa: ANN001
    """标的收盘（本地）：ETF 按代码后缀；指数按映射（详情未同步时
    按品种默认映射兜底）。"""
    from ..core.constant import Exchange
    from .option_sync import CFFEX_DEFAULTS, THS_INDEX_MAP

    try:
        if underlying_code and underlying_code.isdigit():
            # 期权标的 6 位代码（510050）→ 交易所后缀未知则默认 .SH
            symbol = underlying_code + ".SH"
            for exch in (Exchange.SSE, Exchange.SZSE):
                bars = _bars_tail(data_engine, symbol, exch, 1)
                if bars:
                    return float(bars[-1].close_price or 0)
        elif underlying_code:
            ths = THS_INDEX_MAP.get(underlying_code)
            if ths:
                bars = _bars_tail(data_engine, ths, Exchange.SSE, 1)
                if bars:
                    return float(bars[-1].close_price or 0)
        # 详情未同步（underlying_code 为 None/1B0300 无映射）：按品种默认
        if variety_hint:
            ths = CFFEX_DEFAULTS.get(variety_hint, {}).get("underlying_ths")
            if ths:
                bars = _bars_tail(data_engine, ths, Exchange.SSE, 1)
                if bars:
                    return float(bars[-1].close_price or 0)
        # 本地无标的行情：用 put-call parity 粗反推（近似：S ≈ C - P + K）
        # 缺对手腿数据时返回 0
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _atm_iv_term_safe(data_engine, underlying_id: str) -> list[dict]:  # noqa: ANN001
    """期权标的的 ATM IV 期限结构（复用 options 路由的核心逻辑）。"""
    try:
        from .option_iv import atm_iv_term

        return atm_iv_term(data_engine, underlying_id)
    except Exception:  # noqa: BLE001
        return []
