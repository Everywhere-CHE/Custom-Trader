"""行情数据端点：合约、Tick 快照、历史 K 线，
以及用户自选列表。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.constant import Exchange, Interval
from ...core.engine.main_engine import MainEngine
from ...core.object import HistoryRequest, SubscribeRequest
from ...core.utility import extract_vt_symbol
from ...db.data_engine import DataEngine
from ...watchlist import add_symbol, load_watchlist, remove_symbol
from ..deps import get_main_engine
from ..schemas import serialize_bar, serialize_contract, serialize_tick

router = APIRouter(tags=["market"])


def _aggregate_bars(bars: list, interval: Interval) -> list:
    """将 1m K 线聚合到更高周期（5m/15m/30m/1h/d/w），不扫 tick。

    按桶分组：open=首根开盘，high=最高，low=最低，close=末根收盘，
    volume=成交量之和。输入按时间升序。
    """
    from ...core.object import BarData
    from ...core.utility import bucket_start

    result: list[BarData] = []
    current: BarData | None = None
    current_bucket = None
    for bar in bars:
        if bar.datetime is None:
            continue
        bucket = bucket_start(bar.datetime, interval)
        if current is None or bucket != current_bucket:
            if current is not None:
                result.append(current)
            current = BarData(
                gateway_name=bar.gateway_name,
                symbol=bar.symbol,
                exchange=bar.exchange,
                datetime=bucket,
                interval=interval,
                open_price=bar.open_price,
                high_price=bar.high_price,
                low_price=bar.low_price,
                close_price=bar.close_price,
                volume=bar.volume,
            )
            current_bucket = bucket
        else:
            current.high_price = max(current.high_price, bar.high_price)
            current.low_price = min(current.low_price, bar.low_price)
            current.close_price = bar.close_price
            current.volume += bar.volume
    if current is not None:
        result.append(current)
    return result


@router.get("/contracts")
def list_contracts(
    gateway: str = "",
    product: str = "",
    keyword: str = "",
    futures: bool = False,
    limit: int = 0,
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    contracts = main_engine.oms.get_all_contracts()
    if futures:
        contracts = [c for c in contracts if c.product.value == "FUTURES"]
    if gateway:
        contracts = [c for c in contracts if c.gateway_name == gateway]
    if product:
        contracts = [c for c in contracts if c.product.value == product]
    if keyword:
        kw = keyword.lower()
        contracts = [
            c
            for c in contracts
            if kw in c.symbol.lower()
            or kw in c.name.lower()
            or kw in c.vt_symbol.lower()
        ]
    if limit > 0:
        contracts = contracts[:limit]
    return [serialize_contract(c) for c in contracts]


def _extract_vt_symbol_or_raise(vt_symbol: str):
    """vt_symbol → (symbol, Exchange)；非法抛 ValueError（供复用）。"""
    try:
        return extract_vt_symbol(vt_symbol)
    except ValueError as e:
        raise ValueError(f"非法合约代码: {vt_symbol}") from None


# ----------------------------------------------------------------------
# 自选列表
# ----------------------------------------------------------------------


def _subscribe_watch(vt_symbol: str, main_engine: MainEngine) -> None:
    """在其所属（或支持该合约的）网关上订阅自选列表合约。"""
    try:
        symbol, exchange = extract_vt_symbol(vt_symbol)
    except ValueError:
        raise HTTPException(422, f"非法合约代码: {vt_symbol}") from None

    contract = main_engine.oms.get_contract(vt_symbol)
    if contract:
        gateway_name = contract.gateway_name
        # 如果合约来自 SIM，但已连接了支持该交易所的真实行情
        # 网关（CTP），则优先使用真实网关。
        if gateway_name == "SIM":
            for name, gw in main_engine.gateways.items():
                if name != "SIM" and gw.connected and exchange in gw.exchanges:
                    gateway_name = name
                    break
    else:
        # 未知合约：路由到已连接且覆盖该交易所的网关。
        # 优先使用真实行情网关（STOCK 会懒注册任意 A 股代码），
        # 其次才是 SIM，因为 SIM 只认识其内置合约。
        # 只路由到真实行情网关（CTP/STOCK）；FUYAO（纯数据）与 SIM
        # 不能交付实时 tick，不参与订阅
        candidates = [
            name
            for name, gateway in main_engine.gateways.items()
            if name != "SIM"
            and not getattr(gateway, "data_only", False)
            and gateway.connected
            and exchange in gateway.exchanges
        ]
        gateway_name = candidates[0] if candidates else ""
        if not gateway_name:
            raise HTTPException(
                404,
                f"没有已连接的网关支持交易所 {exchange.value}"
                f"（期货合约需先连接 CTP）",
            )
    main_engine.subscribe(
        SubscribeRequest(symbol=symbol, exchange=exchange), gateway_name
    )


@router.get("/watchlist")
def get_watchlist(main_engine: MainEngine = Depends(get_main_engine)) -> dict:
    """关注列表（带 kind/name 元数据）+ 实时摘要。"""
    from ...services.watchlist_view import summary

    return summary(main_engine)


@router.post("/watchlist", status_code=201)
def add_to_watchlist(
    body: dict,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """加入关注：接受 {vt_symbol} 或 {id, kind?, name?}。

    股票/期货照旧订阅行情；期权（标的/合约）不做实时订阅
    （端外无期权快照，价格走日K）。
    """
    from ...watchlist import add_entry, _infer_kind

    item_id = str(body.get("vt_symbol") or body.get("id") or "").strip()
    if not item_id:
        raise HTTPException(422, "vt_symbol/id 不能为空")
    kind = str(body.get("kind") or "").strip() or None
    name = str(body.get("name") or "").strip() or None
    if kind and kind not in ("stock", "future", "option_underlying", "option_contract"):
        raise HTTPException(422, f"非法 kind: {kind}")
    entry_kind = kind or _infer_kind(item_id)
    entry = add_entry(item_id, kind=entry_kind, name=name)
    if entry_kind in ("stock", "future"):
        try:
            _subscribe_watch(item_id, main_engine)
        except HTTPException:
            # 关注先落库；订阅失败（如期货网关未连）不阻塞添加
            pass
    if entry_kind in ("future", "option_contract"):
        # 同时登记 FUYAO 分时数据源（真实分钟级行情；CTP 只负责交易）
        try:
            symbol, exchange = extract_vt_symbol(item_id)
            fuyao = next(
                (
                    gw
                    for gw in main_engine.gateways.values()
                    if gw.connected and getattr(gw, "data_only", False)
                ),
                None,
            )
            if fuyao is not None:
                main_engine.subscribe(
                    SubscribeRequest(symbol=symbol, exchange=exchange),
                    fuyao.gateway_name,
                )
        except HTTPException:
            pass
    return {"item": entry, "symbols": load_watchlist()}


@router.delete("/watchlist/{vt_symbol}")
def remove_from_watchlist(vt_symbol: str) -> dict:
    from ...watchlist import remove_entry

    remove_entry(vt_symbol)
    return {"ok": True}


@router.get("/watchlist/detail")
def watchlist_detail(
    id: str = Query(..., description="关注条目 id（vt_symbol 或期权 thscode/标的）"),
    kind: str | None = Query(None),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """单标的聚合详情（行情/关键价位/异动/告警/期权摘要）。"""
    from ...services.watchlist_view import detail

    return detail(main_engine, id, kind)


@router.get("/ticks")
def list_ticks(
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    return [serialize_tick(t) for t in main_engine.oms.get_all_ticks()]


@router.get("/ticks/{vt_symbol}")
def get_tick(
    vt_symbol: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    tick = main_engine.oms.get_tick(vt_symbol)
    if not tick:
        raise HTTPException(404, f"No tick for {vt_symbol} (not subscribed?)")
    return serialize_tick(tick)


@router.get("/bars/intraday")
def get_intraday_bars(
    vt_symbol: str = Query(...),
    interval: str = "1m",
    limit: int = 500,
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    """图表 K 线。

    - 1m：仅使用真实记录的 Tick（纯实时日内数据，无合成）。
    - 1h/d/w：从 K 线存储中读取数月历史（首次使用时自动从
      合约所属网关下载），并与最近一段时间由真实 Tick 聚合
      而成的 K 线合并 — 实时尾部数据始终覆盖重叠的历史数据。
    """
    try:
        symbol, exchange = extract_vt_symbol(vt_symbol)
        bar_interval = Interval(interval)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    data_engine = main_engine.get_engine("data")
    assert isinstance(data_engine, DataEngine)

    if bar_interval == Interval.MINUTE:
        # 1m 实时图：只读尾部 K 线 + BarRecorder 内存中的当前分钟
        # （不扫 tick，避免首次加载时的大量 tick 扫描）
        bars = data_engine.load_bars_tail(symbol, exchange, bar_interval, limit)
        bar_recorder = main_engine.get_engine("bar_recorder")
        if bar_recorder is not None:
            get_current = getattr(bar_recorder, "get_current_bar", None)
            if get_current is not None:
                current = get_current(vt_symbol)
                if current is not None:
                    # 当前分钟与最后一条已落库的 K 线不同分钟才追加
                    if not bars or bars[-1].datetime < current.datetime:
                        bars.append(current)
        return [serialize_bar(b) for b in bars]

    if bar_interval in (Interval.MINUTE5, Interval.MINUTE15, Interval.MINUTE30):
        # 5m/15m/30m：从 1m bars 表聚合（不扫 tick）
        minutes_per = {"5m": 5, "15m": 15, "30m": 30}[bar_interval.value]
        m1_bars = data_engine.load_bars_tail(
            symbol, exchange, Interval.MINUTE, limit * minutes_per + minutes_per
        )
        bar_recorder = main_engine.get_engine("bar_recorder")
        if bar_recorder is not None:
            get_current = getattr(bar_recorder, "get_current_bar", None)
            if get_current is not None:
                cur = get_current(vt_symbol)
                if cur is not None and (not m1_bars or m1_bars[-1].datetime < cur.datetime):
                    m1_bars.append(cur)
        agg = _aggregate_bars(m1_bars, bar_interval)
        return [serialize_bar(b) for b in agg[-limit:]]

    # ----- 更高时间周期 (1h/d/w)：深度历史 + 实时尾部 -----
    from ...core.utility import bucket_start

    live_bars = data_engine.load_intraday_bars(
        symbol, exchange, limit=limit, interval=bar_interval
    )

    history = data_engine.load_bars(symbol, exchange, bar_interval)
    # 忽略时间戳未对齐到桶边界的旧数据行（旧版生成器的产物），
    # 否则它们会在图表上渲染成第二条时间错位的线。
    history = [
        b
        for b in history
        if b.datetime is not None
        and bucket_start(b.datetime, bar_interval) == b.datetime
    ]

    # 当存储为空，或深度不足以满足目标深度时下载
    # （例如目前只记录了几天的数据）。
    months = {"1h": 3, "d": 12, "w": 24}.get(interval, 6)
    target_start = datetime.now() - timedelta(days=months * 30)
    too_shallow = (
        not history
        or history[0].datetime is None
        or history[0].datetime > target_start + timedelta(days=months * 15)
    )
    if too_shallow:
        contract = main_engine.oms.get_contract(vt_symbol)
        if contract and contract.history_data:
            req = HistoryRequest(
                symbol=symbol,
                exchange=exchange,
                start=target_start,
                end=datetime.now(),
                interval=bar_interval,
            )
            downloaded = main_engine.query_history(req, contract.gateway_name)
            if downloaded:
                data_engine.save_bars(downloaded)
                history = data_engine.load_bars(symbol, exchange, bar_interval)

    # 合并：先放历史数据，再用 Tick 聚合的实时 K 线覆盖重叠部分。
    merged: dict = {}
    for bar in history:
        if bar.datetime is not None:
            merged[bucket_start(bar.datetime, bar_interval)] = bar
    for bar in live_bars:
        if bar.datetime is not None:
            merged[bucket_start(bar.datetime, bar_interval)] = bar

    bars = []
    for key in sorted(merged):
        bar = merged[key]
        bar.datetime = key
        bars.append(bar)
    if limit > 0:
        bars = bars[-limit:]
    return [serialize_bar(b) for b in bars]


@router.get("/bars")
def get_bars(
    vt_symbol: str = Query(...),
    interval: str = Query("1m"),
    start: datetime | None = None,
    end: datetime | None = None,
    from_gateway: bool = Query(
        False,
        description="If true, query the gateway (generates simulated bars on SIM) "
        "and persist them; otherwise read from the database.",
    ),
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    try:
        symbol, exchange = extract_vt_symbol(vt_symbol)
        bar_interval = Interval(interval)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    data_engine = main_engine.get_engine("data")
    assert isinstance(data_engine, DataEngine)

    bars = data_engine.load_bars(symbol, exchange, bar_interval, start, end)
    # 期权合约兜底：CTP 订阅的期权（如 CZCE/SHFE 商品期权）本地
    # 无日K时，经 fuyao 期权日K接口补拉（仅日线；端外无期权分钟线）
    if not bars and bar_interval == Interval.DAILY and not from_gateway:
        try:
            from datetime import timedelta as _td

            from ...services.option_sync import ensure_option_bars_for_symbol

            _end = end or datetime.now()
            _start = start or (_end - _td(days=120))
            fallback = ensure_option_bars_for_symbol(
                data_engine, symbol, exchange.value, _start, _end
            )
            if fallback:
                bars = [
                    b
                    for b in fallback
                    if (start is None or b.datetime >= start)
                    and (end is None or b.datetime <= end)
                ]
        except Exception:  # noqa: BLE001 —— 兜底失败按无数据处理
            pass

    if from_gateway:
        contract = main_engine.oms.get_contract(vt_symbol)
        if not contract:
            raise HTTPException(404, f"Contract not found: {vt_symbol}")
        from datetime import timedelta

        from ...core.object import HistoryRequest

        req = HistoryRequest(
            symbol=symbol,
            exchange=exchange,
            start=start or (datetime.now() - timedelta(days=5)),
            end=end,
            interval=bar_interval,
        )
        bars = main_engine.query_history(req, contract.gateway_name)
        if bars:
            data_engine.save_bars(bars)
        return [serialize_bar(b) for b in bars]

    # 非网关路径：优先用前面已取到的 bars（含期权日K兜底结果）
    if not bars:
        bars = data_engine.load_bars(symbol, exchange, bar_interval, start, end)
    return [serialize_bar(b) for b in bars]


@router.get("/key-levels")
def get_key_levels_endpoint(
    vt_symbol: str = Query(...),
    days: int = Query(250, ge=30, le=800),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """9 大类关键价位（筹码密集区/枢轴点/前高前低/布林/Keltner/ATR/缺口/Fib/整数关口）。

    基于本地日 K 计算；本地数据不足 60 根时自动经网关补拉历史
    （日线在 FUYAO 已连接时优先走 FUYAO）。
    """
    from ...services.key_levels import compute_key_levels_for

    try:
        return compute_key_levels_for(main_engine, vt_symbol, days)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from None

