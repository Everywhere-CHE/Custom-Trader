"""交易端点：下单 / 查询 / 撤单、成交列表。

订单/成交查询会将内存中的 OMS 状态（实时，当前会话）与
数据库历史（由 DataEngine 持久化）合并，因此重启后记录
仍然可见。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from ...core.engine.main_engine import MainEngine, OrderRejectedError
from ...core.constant import Offset
from ...core.object import CancelRequest, OrderRequest
from ...core.utility import extract_vt_orderid, extract_vt_symbol
from ...db.data_engine import DataEngine
from ..deps import get_main_engine
from ..schemas import PlaceOrderBody, serialize_order, serialize_trade

router = APIRouter(tags=["trading"])


def _get_data_engine(main_engine: MainEngine) -> DataEngine | None:
    engine = main_engine.get_engine("data")
    return engine if isinstance(engine, DataEngine) else None


@router.post("/orders", status_code=201)
def place_order(
    body: PlaceOrderBody,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    contract = main_engine.oms.get_contract(body.vt_symbol)
    if not contract:
        raise HTTPException(422, f"Contract not found: {body.vt_symbol}")

    gateway_name = body.gateway_name or contract.gateway_name
    gateway = main_engine.gateways.get(gateway_name)
    if not gateway:
        raise HTTPException(404, f"Gateway not found: {gateway_name}")
    if not gateway.connected:
        raise HTTPException(409, f"Gateway not connected: {gateway_name}")

    symbol, exchange = extract_vt_symbol(body.vt_symbol)
    req = OrderRequest(
        symbol=symbol,
        exchange=exchange,
        direction=body.direction,
        type=body.type,
        volume=body.volume,
        price=body.price,
        offset=body.offset,
        reference=body.reference,
    )

    # 开平转换（上期所/能源中心拆分平今/平昨）
    reqs = main_engine.oms.convert_order_request(req, gateway_name)

    vt_orderids: list[str] = []
    for converted in reqs:
        try:
            vt_orderid = main_engine.send_order(
                converted, gateway_name, raise_on_reject=True
            )
            if vt_orderid:
                vt_orderids.append(vt_orderid)
                main_engine.oms.update_order_request(
                    converted, vt_orderid, gateway_name
                )
        except OrderRejectedError as e:
            if not vt_orderids:
                raise HTTPException(422, e.reason) from None
            main_engine.write_log(
                f"order rejected [{converted.offset.value} x{converted.volume}]: {e.reason}",
                source="API",
                level=logging.WARNING,
            )

    if not vt_orderids:
        raise HTTPException(502, f"网关未接受订单: {gateway_name}")

    return {"vt_orderid": vt_orderids[0]}


@router.get("/orders")
@router.head("/orders", include_in_schema=False)
def list_orders(
    active_only: bool = False,
    vt_symbol: str = "",
    history: bool = True,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    if active_only:
        return [
            serialize_order(o)
            for o in main_engine.oms.get_all_active_orders(vt_symbol)
        ]

    # 内存中的实时订单（当前会话，状态最新）
    orders = main_engine.oms.get_all_orders()
    if vt_symbol:
        orders = [o for o in orders if o.vt_symbol == vt_symbol]
    merged = {o.vt_orderid: serialize_order(o) for o in orders}

    # 合并已持久化的历史（可跨重启保留）；重复时以内存为准
    if history:
        data_engine = _get_data_engine(main_engine)
        if data_engine:
            for order in data_engine.load_orders(vt_symbol, start, end, limit):
                merged.setdefault(order.vt_orderid, serialize_order(order))

    result = list(merged.values())
    result.sort(key=lambda o: o.get("datetime") or "", reverse=True)
    return result[:limit] if limit > 0 else result


@router.get("/orders/{vt_orderid}")
def get_order(
    vt_orderid: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    order = main_engine.oms.get_order(vt_orderid)
    if not order:
        raise HTTPException(404, f"Order not found: {vt_orderid}")
    return serialize_order(order)


@router.delete("/orders/{vt_orderid}", status_code=202)
def cancel_order(
    vt_orderid: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    order = main_engine.oms.get_order(vt_orderid)
    # 同时尝试从数据库加载（可跨重启保留）
    if not order:
        data_engine = _get_data_engine(main_engine)
        if data_engine:
            for o in data_engine.load_orders(limit=1000):
                if o.vt_orderid == vt_orderid:
                    order = o
                    break
    if not order:
        raise HTTPException(404, f"Order not found: {vt_orderid}")
    if not order.is_active():
        raise HTTPException(409, f"Order not active: {order.status.value}")

    gateway_name, _ = extract_vt_orderid(vt_orderid)
    req: CancelRequest = order.create_cancel_request()
    main_engine.cancel_order(req, gateway_name)
    return {"accepted": True, "vt_orderid": vt_orderid}


@router.get("/trades")
def list_trades(
    vt_symbol: str = "",
    history: bool = True,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    # 内存中的实时成交（当前会话）
    trades = main_engine.oms.get_all_trades()
    if vt_symbol:
        trades = [t for t in trades if t.vt_symbol == vt_symbol]

    # 构建合约查找表，用于盈亏计算
    contracts = {c.vt_symbol: c for c in main_engine.oms.get_all_contracts()}

    # 加载已持久化的成交，用于盈亏匹配
    data_engine = _get_data_engine(main_engine)

    # 收集全部成交（先数据库，后当前会话）并按时间排序，
    # 使盈亏匹配器先看到 OPEN 记录再看到 CLOSE 记录。
    all_trades: list = []
    if history and data_engine:
        for t in data_engine.load_trades(vt_symbol, start, end, limit):
            all_trades.append(t)
    all_trades.extend(trades)
    all_trades.sort(key=lambda t: t.datetime or datetime.min, reverse=False)

    # 按时间顺序扫描全部成交，预先填充 open_positions。
    # CLOSE 成交与 OPEN 记录进行匹配，得到的盈亏
    # 存入查找字典。
    contracts_map = {c.vt_symbol: c for c in main_engine.oms.get_all_contracts()}
    open_positions: dict[str, list[tuple[float, float]]] = {}
    pnl_lookup: dict[str, dict] = {}  # vt_tradeid -> {pnl, fee, net_pnl, entry_price}

    for t in all_trades:
        offset_str = t.offset.value if hasattr(t.offset, 'value') else str(t.offset)
        if offset_str not in ("OPEN", "CLOSE"):
            continue
        ct = contracts_map.get(t.vt_symbol)
        size = ct.size if ct else 1
        key = t.vt_symbol

        if offset_str == "OPEN":
            open_positions.setdefault(key, []).append((t.price, t.volume))
        else:
            # CLOSE：与最近的 OPEN 记录匹配
            entries = open_positions.get(key, [])
            if not entries:
                continue
            remaining_vol = t.volume
            matched_entry = None
            for i, (ep, ev) in enumerate(entries):
                if ev <= 0:
                    continue
                if matched_entry is None:
                    matched_entry = ep
                if ev >= remaining_vol:
                    entries[i] = (ep, ev - remaining_vol)
                    remaining_vol = 0
                    break
                else:
                    remaining_vol -= ev
                    entries[i] = (ep, 0)
            if matched_entry is None:
                continue
            open_positions[key] = [(ep, ev) for ep, ev in entries if ev > 0]
            if t.direction and t.direction.value == "SHORT":
                diff = t.price - matched_entry
            else:
                diff = matched_entry - t.price
            pnl = round(diff * size * t.volume, 2)
            notional = t.price * size * t.volume
            fee = round(abs(notional * 0.0003), 2)  # 约 0.03% 手续费
            net_pnl = round(pnl - fee, 2)
            pnl_lookup[t.vt_tradeid] = {
                "pnl": pnl,
                "fee": fee,
                "net_pnl": net_pnl,
                "entry_price": matched_entry,
            }

    def _calc_pnl(trade) -> dict:
        return pnl_lookup.get(trade.vt_tradeid, {"pnl": 0.0, "fee": 0.0, "net_pnl": 0.0, "entry_price": 0.0})

    def _serialize(trade) -> dict:
        result = serialize_trade(trade)
        pnl_info = _calc_pnl(trade)
        result["pnl"] = pnl_info["net_pnl"]
        result["pnl_gross"] = pnl_info["pnl"]
        result["fee"] = pnl_info["fee"]
        result["entry_price"] = pnl_info["entry_price"]
        return result

    merged = {t.vt_tradeid: _serialize(t) for t in trades}

    # 合并已持久化的历史（可跨重启保留）
    if history and data_engine:
        for trade in data_engine.load_trades(vt_symbol, start, end, limit):
            if trade.vt_tradeid not in merged:
                merged[trade.vt_tradeid] = _serialize(trade)

    result = list(merged.values())
    result.sort(key=lambda t: t.get("datetime") or "", reverse=True)
    return result[:limit] if limit > 0 else result


# ----------------------------------------------------------------------
# 历史记录管理
# ----------------------------------------------------------------------


@router.delete("/orders/{vt_orderid}/history", status_code=200)
def delete_order_history(
    vt_orderid: str,
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """从数据库中删除单条历史订单。"""
    data_engine = _get_data_engine(main_engine)
    if not data_engine:
        raise HTTPException(503, "DataEngine not available")
    deleted = data_engine.delete_order(vt_orderid)
    if not deleted:
        # 同时尝试匹配内存中的 OMS（从缓存中移除）
        order = main_engine.oms.get_order(vt_orderid)
        if order:
            main_engine.oms.orders.pop(order.vt_orderid, None)
            return {"deleted": True, "vt_orderid": vt_orderid}
        raise HTTPException(404, f"Order not found: {vt_orderid}")
    return {"deleted": True, "vt_orderid": vt_orderid}


@router.delete("/history/orders", status_code=200)
def clear_order_history(
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """清空数据库和内存缓存中的全部订单历史。"""
    data_engine = _get_data_engine(main_engine)
    if not data_engine:
        raise HTTPException(503, "DataEngine not available")

    # 检查活动订单 — 提示但仍继续执行
    active = main_engine.oms.get_all_active_orders()
    db_count = data_engine.clear_all_orders()
    # 同时清空内存中的 OMS 缓存，使订单无需重启
    # 即可立即从 UI 中消失。
    oms_count = main_engine.oms.clear_orders()
    return {
        "deleted": db_count,
        "cache_cleared": oms_count,
        "active_orders_skipped": len(active),
    }


@router.delete("/history/trades", status_code=200)
def clear_trade_history(
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """清空数据库和内存缓存中的全部成交历史。"""
    data_engine = _get_data_engine(main_engine)
    if not data_engine:
        raise HTTPException(503, "DataEngine not available")
    db_count = data_engine.clear_all_trades()
    # 同时清空内存中的 OMS 缓存
    oms_count = main_engine.oms.clear_trades()
    return {"deleted": db_count, "cache_cleared": oms_count}
