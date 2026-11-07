"""用户自选列表：跨 A股/期货/期权标的/期权合约的统一关注列表。

存储 ``data/watchlist.json``，条目为带元数据的对象：

    {"id": "600519.SSE", "kind": "stock", "name": "贵州茅台",
     "added_at": "..."}

kind 推断规则：
- ``.SSE/.SZSE/.BSE`` 后缀 → stock
- ``.DCE/.SHFE/.CZCE/.INE/.GFEX/.CFFEX`` 后缀 → future
- 在 ``option_contracts`` 表中（thscode 命中）→ option_contract
- 匹配期权品种（510050O / IO / MO / HO）→ option_underlying

向后兼容：旧版纯字符串数组在首次读取时自动迁移为 entry 结构；
:func:`load_watchlist` 保持返回 ``list[str]`` 供既有消费方
（monitor / market_regime / data_health / 自动订阅）零改动使用。
"""

import json
from datetime import datetime

from .config import DATA_DIR
from .core.utility import CHINA_TZ

CONFIG_PATH = DATA_DIR / "watchlist.json"

# 首次运行时的默认自选列表：仅包含真实 A 股代码（由 STOCK
# Gateway 惰性注册；名称来自实时行情推送）。
# 期货合约在 CTP 连接后出现，之后即可添加。
DEFAULT_WATCHLIST = [
    "600519.SSE",
    "601318.SSE",
    "510300.SSE",
    "000001.SZSE",
    "300750.SZSE",
]

_STOCK_SUFFIXES = (".SSE", ".SZSE", ".BSE")
_FUTURE_SUFFIXES = (".DCE", ".SHFE", ".CZCE", ".INE", ".GFEX", ".CFFEX")

VALID_KINDS = ("stock", "future", "option_underlying", "option_contract")


def _kind_by_suffix(item_id: str) -> str | None:
    if item_id.endswith(_STOCK_SUFFIXES):
        return "stock"
    if item_id.endswith(_FUTURE_SUFFIXES):
        return "future"
    return None


def _bare_symbol(item_id: str) -> str:
    """去掉交易所后缀的裸代码（MA701P2800.CZCE → MA701P2800）。"""
    return item_id.rsplit(".", 1)[0] if "." in item_id else item_id


def _dashed_option_ticker(bare: str) -> str | None:
    """DCE 期权 CTP 代码 → fuyao 横线格式（i2701C700 → i2701-C-700）。"""
    import re

    m = re.match(r"^([A-Za-z]+\d+)([CP])(\d+)$", bare)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _infer_kind(item_id: str) -> str:
    """推断关注条目类型；无法识别时按后缀兜底为 stock/future。"""
    item_id = item_id.strip()
    by_suffix = _kind_by_suffix(item_id)
    if by_suffix in ("stock", "future"):
        # 股票/期货后缀的也可能是期权标的（510050）或 CTP 期权合约
        # （MA701P2800.CZCE）—— 查期权库确认
        kind = _option_kind(item_id)
        if kind:
            return kind
    if by_suffix:
        return by_suffix
    # 纯数字（510050）或品种代码（IO/MO/HO）→ 期权标的候选
    kind = _option_kind(item_id)
    if kind:
        return kind
    # 期货 CTP 代码（如 rb2601 不带后缀的写入场景）
    return "future" if item_id and not item_id[0].isdigit() else "stock"


def _option_kind(item_id: str) -> str | None:
    """查本地期权目录判定是合约还是标的。

    合约匹配支持两种形态：fuyao thscode（10011425.SH / IO2612-C-4000.CFE）
    与 CTP 代码（MA701P2800.CZCE → ticker=MA701P2800 + 交易所）。
    """
    try:
        from sqlalchemy import select

        from .db.models import OptionContractModel, OptionVarietyModel
        from .db.session import create_session

        session = create_session()
        try:
            if (
                session.query(OptionContractModel.id)
                .filter(OptionContractModel.thscode == item_id)
                .first()
            ):
                return "option_contract"
            # CTP 形态：裸代码 + 交易所后缀（DCE 期权 fuyao ticker
            # 为横线格式 i2701C700 → i2701-C-700，两种形态都试）
            bare = _bare_symbol(item_id)
            if bare != item_id and bare:
                exch = item_id.rsplit(".", 1)[1]
                candidates = [
                    bare,
                    bare.upper(),
                    _dashed_option_ticker(bare),
                    (_dashed_option_ticker(bare) or "").upper() or None,
                ]
                hit = session.execute(
                    select(OptionContractModel.thscode).where(
                        OptionContractModel.ticker.in_(
                            [c for c in candidates if c]
                        ),
                        OptionContractModel.exchange_code == exch,
                    )
                ).first()
                if hit:
                    return "option_contract"
            vid = item_id if not item_id.isdigit() else item_id + "O"
            if (
                session.query(OptionVarietyModel.id)
                .filter(OptionVarietyModel.variety_code == vid)
                .first()
            ):
                return "option_underlying"
        finally:
            session.close()
    except Exception:  # noqa: BLE001 —— 表不存在等场景按未知处理
        pass
    return None


def resolve_name(item_id: str, kind: str) -> str:
    """按 kind 解析展示名称（解析不到回退代码本身）。"""
    try:
        if kind == "stock":
            # 全市场名单（market_sync 落盘）
            symbols_file = DATA_DIR / "market_symbols.json"
            if symbols_file.exists():
                data = json.loads(symbols_file.read_text(encoding="utf-8"))
                symbol = item_id.split(".")[0]
                for it in data if isinstance(data, list) else []:
                    if it.get("symbol") == symbol:
                        return it.get("name") or item_id
            # 回退：从 contracts 表（网关注册过）
            from .db.models import ContractModel
            from .db.session import create_session

            session = create_session()
            try:
                row = (
                    session.query(ContractModel.name)
                    .filter(
                        ContractModel.symbol == item_id.split(".")[0],
                        ContractModel.gateway_name == "STOCK",
                    )
                    .first()
                )
            finally:
                session.close()
            return (row[0] if row and row[0] else item_id)
        if kind == "future":
            from .db.models import ContractModel
            from .db.session import create_session

            session = create_session()
            try:
                row = (
                    session.query(ContractModel.name)
                    .filter(ContractModel.symbol == item_id.split(".")[0])
                    .first()
                )
            finally:
                session.close()
            return (row[0] if row and row[0] else item_id)
        # 期权
        from .db.models import OptionContractModel, OptionVarietyModel
        from .db.session import create_session

        session = create_session()
        try:
            if kind == "option_contract":
                row = (
                    session.query(OptionContractModel.name)
                    .filter(OptionContractModel.thscode == item_id)
                    .first()
                )
                return (row[0] if row and row[0] else item_id)
            vid = item_id if not item_id.isdigit() else item_id + "O"
            row = (
                session.query(OptionVarietyModel.name)
                .filter(OptionVarietyModel.variety_code == vid)
                .first()
            )
            return (row[0] if row and row[0] else item_id)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 —— 名称解析失败不影响加入
        return item_id


def normalize_entry(raw) -> dict | None:  # noqa: ANN001
    """把任意旧/新格式条目归一为 entry dict；非法返回 None。"""
    if isinstance(raw, str):
        item_id = raw.strip()
        if not item_id:
            return None
        kind = _infer_kind(item_id)
        return {
            "id": item_id,
            "kind": kind,
            "name": resolve_name(item_id, kind),
            "added_at": datetime.now(CHINA_TZ).isoformat(),
        }
    if isinstance(raw, dict):
        item_id = str(raw.get("id") or raw.get("vt_symbol") or "").strip()
        if not item_id:
            return None
        kind = str(raw.get("kind") or "").strip()
        if kind not in VALID_KINDS:
            kind = _infer_kind(item_id)
        return {
            "id": item_id,
            "kind": kind,
            "name": str(raw.get("name") or "") or resolve_name(item_id, kind),
            "added_at": str(raw.get("added_at") or "")
            or datetime.now(CHINA_TZ).isoformat(),
        }
    return None


_kind_upgrade_done = False


def _upgrade_stale_kinds(entries: list[dict]) -> list[dict]:
    """进程内一次性：把旧版误判为 future 的期权合约条目升级。"""
    global _kind_upgrade_done
    if _kind_upgrade_done:
        return entries
    _kind_upgrade_done = True
    changed = False
    for e in entries:
        if e["kind"] != "future":
            continue
        kind = _option_kind(e["id"])
        if kind == "option_contract":
            e["kind"] = kind
            changed = True
    if changed:
        _save_entries_locked(entries)
    return entries


def _migrate_and_load() -> list[dict]:
    """读取并迁移旧格式；返回 entry 列表。文件损坏回退默认。"""
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries: list[dict] = []
                seen: set[str] = set()
                migrated = False
                for raw in data:
                    entry = normalize_entry(raw)
                    if entry is None:
                        migrated = True  # 丢弃非法条目也算变更
                        continue
                    if isinstance(raw, str) or "kind" not in (raw or {}):
                        migrated = True
                    if entry["id"] not in seen:
                        seen.add(entry["id"])
                        entries.append(entry)
                if migrated:
                    _save_entries_locked(entries)
                return _upgrade_stale_kinds(entries)
    except Exception:  # noqa: BLE001
        pass
    return _upgrade_stale_kinds(
        [normalize_entry(s) for s in DEFAULT_WATCHLIST if normalize_entry(s)]
    )


def _save_entries_locked(entries: list[dict]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ----------------------------------------------------------------------
# 公共接口
# ----------------------------------------------------------------------

def load_watchlist_entries() -> list[dict]:
    """完整 entry 列表（id/kind/name/added_at）。"""
    return _migrate_and_load()


def load_watchlist() -> list[str]:
    """兼容接口：仅 id 列表（monitor/regime/data_health 等消费方）。"""
    return [e["id"] for e in _migrate_and_load()]


def save_watchlist(symbols: list[str]) -> list[str]:
    """兼容接口：按 id 全量覆盖保存（去重保序）。"""
    entries = _migrate_and_load()
    by_id = {e["id"]: e for e in entries}
    seen: set[str] = set()
    cleaned: list[dict] = []
    for s in symbols:
        sid = str(s).strip()
        if sid and sid not in seen:
            seen.add(sid)
            cleaned.append(by_id.get(sid) or normalize_entry(sid))
    _save_entries_locked(cleaned)
    return [e["id"] for e in cleaned]


def add_entry(item_id: str, kind: str | None = None, name: str | None = None) -> dict:
    """加入关注；已存在时仅刷新名称。返回该条目。"""
    item_id = item_id.strip()
    entries = _migrate_and_load()
    existing = next((e for e in entries if e["id"] == item_id), None)
    if existing is not None:
        if name:
            existing["name"] = name
        _save_entries_locked(entries)
        return existing
    if kind not in VALID_KINDS:
        kind = _infer_kind(item_id)
    entry = {
        "id": item_id,
        "kind": kind,
        "name": name or resolve_name(item_id, kind),
        "added_at": datetime.now(CHINA_TZ).isoformat(),
    }
    entries.append(entry)
    _save_entries_locked(entries)
    return entry


def remove_entry(item_id: str) -> list[str]:
    entries = [e for e in _migrate_and_load() if e["id"] != item_id]
    _save_entries_locked(entries)
    return [e["id"] for e in entries]


# 旧函数名兼容（MarketList 旧调用路径 / 外部脚本）
def add_symbol(vt_symbol: str) -> list[str]:
    add_entry(vt_symbol)
    return load_watchlist()


def remove_symbol(vt_symbol: str) -> list[str]:
    return remove_entry(vt_symbol)
