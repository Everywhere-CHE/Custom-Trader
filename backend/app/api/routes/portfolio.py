"""投资组合端点：持仓与账户。"""

from fastapi import APIRouter, Depends

from ...core.constant import Direction
from ...core.engine.main_engine import MainEngine
from ...core.object import PositionData
from ..deps import get_main_engine
from ..schemas import serialize_account, serialize_position

router = APIRouter(tags=["portfolio"])


@router.get("/positions")
def list_positions(
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    result = []
    for p in main_engine.oms.get_all_positions():
        serialized = serialize_position(p)
        # 计算浮动盈亏（基于当前 tick 价格，弥补 CTP SimNow 不返回 PositionProfit 的问题）
        size = 1
        contract = main_engine.oms.get_contract(p.vt_symbol)
        if contract:
            size = contract.size or 1
        tick = main_engine.oms.get_tick(p.vt_symbol)
        if tick and tick.last_price and p.volume > 0:
            if p.direction == Direction.LONG:
                serialized["pnl"] = round(
                    (tick.last_price - p.price) * p.volume * size, 2
                )
            else:
                serialized["pnl"] = round(
                    (p.price - tick.last_price) * p.volume * size, 2
                )
        else:
            serialized["pnl"] = 0.0
        result.append(serialized)
    return result


@router.get("/accounts")
def list_accounts(
    main_engine: MainEngine = Depends(get_main_engine),
) -> list[dict]:
    return [serialize_account(a) for a in main_engine.oms.get_all_accounts()]
