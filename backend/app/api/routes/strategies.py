"""策略管理端点。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from ...core.engine.main_engine import MainEngine
from ...core.strategy.engine import StrategyEngine
from ...core.strategy.validator import (
    StrategyCodeError,
    validate_filename,
    validate_strategy_code,
)
from ..deps import get_main_engine
from ..schemas import CreateStrategyBody, EditStrategyBody, WriteStrategyFileBody

router = APIRouter(prefix="/strategies", tags=["strategies"])

# backend/strategies — 用户/AI 策略插件目录
STRATEGY_USER_DIR = Path(__file__).resolve().parents[3] / "strategies"


def get_strategy_engine(
    main_engine: MainEngine = Depends(get_main_engine),
) -> StrategyEngine:
    engine = main_engine.get_engine("strategy")
    if not isinstance(engine, StrategyEngine):
        raise HTTPException(503, "StrategyEngine not available")
    return engine


@router.get("")
def list_strategies(
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> list[dict]:
    return engine.get_all_strategy_data()


@router.get("/classes")
def list_strategy_classes(
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> list[dict]:
    return engine.get_strategy_class_info()


@router.post("", status_code=201)
def create_strategy(
    body: CreateStrategyBody,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    try:
        # 同时支持单个 vt_symbol 和多个 vt_symbols
        vt_symbols = body.vt_symbols if body.vt_symbols else ([body.vt_symbol] if body.vt_symbol else [])
        if not vt_symbols:
            raise HTTPException(422, "请提供 vt_symbol 或 vt_symbols")
        # 为每个合约创建一个策略实例
        if len(vt_symbols) == 1:
            strategy = engine.add_strategy(
                body.class_name, body.name, vt_symbols[0], body.setting
            )
            return strategy.get_data()
        # 多个合约：创建带后缀的独立策略
        results = []
        created_names: list[str] = []
        try:
            for i, vt in enumerate(vt_symbols):
                suffix = f"_{i+1}" if i > 0 else ""
                name = f"{body.name}{suffix}"
                s = engine.add_strategy(body.class_name, name, vt, body.setting)
                results.append(s.get_data())
                created_names.append(name)
        except (KeyError, ValueError) as e:
            # 部分失败：回滚已创建的策略，避免脏数据
            for name in created_names:
                try:
                    engine.remove_strategy(name)
                except Exception:
                    pass
            raise HTTPException(409, f"{vt}: {e}") from None
        return {"accepted": True, "count": len(results)}
    except KeyError as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/reload")
def reload_strategy_classes(
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    """重新扫描内置 + 用户策略目录。"""
    engine.load_strategy_classes()
    return {"count": len(engine.classes), "classes": sorted(engine.classes)}


@router.get("/files")
def list_strategy_files() -> list[dict]:
    """列出用户策略文件（backend/strategies/*.py）。"""
    if not STRATEGY_USER_DIR.exists():
        return []
    files = []
    for path in sorted(STRATEGY_USER_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        stat = path.stat()
        files.append(
            {
                "filename": path.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return files


@router.get("/files/{filename}")
def read_strategy_file(filename: str) -> dict:
    """读取用户策略文件的源代码。"""
    try:
        validate_filename(filename)
    except StrategyCodeError as e:
        raise HTTPException(422, str(e)) from None
    target = (STRATEGY_USER_DIR / filename).resolve()
    if STRATEGY_USER_DIR.resolve() not in target.parents or not target.exists():
        raise HTTPException(404, f"文件不存在: {filename}")
    return {"filename": filename, "code": target.read_text(encoding="utf-8")}


@router.post("/files", status_code=201)
def write_strategy_file(
    body: WriteStrategyFileBody,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    """向用户插件目录写入策略源码文件。

    校验文件名和代码（AST 检查：无危险导入/调用，
    必须定义 StrategyTemplate 子类），然后重新加载类注册表，
    使新策略立即可用。
    """
    try:
        validate_filename(body.filename)
        classes_found = validate_strategy_code(body.code)
    except StrategyCodeError as e:
        raise HTTPException(422, str(e)) from None

    STRATEGY_USER_DIR.mkdir(parents=True, exist_ok=True)
    target = (STRATEGY_USER_DIR / body.filename).resolve()
    # 路径逃逸防护（文件名正则已阻止路径分隔符）
    if STRATEGY_USER_DIR.resolve() not in target.parents:
        raise HTTPException(422, "非法路径")

    if target.exists() and not body.overwrite:
        raise HTTPException(
            409, f"文件已存在: {body.filename}（传 overwrite=true 覆盖）"
        )

    target.write_text(body.code, encoding="utf-8")
    engine.load_strategy_classes()

    return {
        "saved": body.filename,
        "path": str(target),
        "classes_found": classes_found,
        "total_classes": len(engine.classes),
    }


@router.get("/reconcile")
def reconcile_positions(
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> list[dict]:
    """对账：比较每个策略的 pos 与网关净持仓。

    可检测策略停止期间的手动交易、丢失的成交回报等造成的
    偏差。matched=false 的行需要关注；shared=true 的行
    （多个策略共用同一合约）无法自动同步。
    """
    return engine.reconcile_positions()


@router.post("/{name}/sync-pos")
def sync_strategy_pos(
    name: str,
    pos: float | None = Query(None, description="手动指定持仓数量，不传则取网关净持仓"),
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    """用网关净持仓覆盖策略的 pos，或手动指定 pos。

    当合约被多个策略共用时，网关无法自动分配净持仓。
    此时需要传入 ``pos`` 手动指定此策略应持有的数量。
    例如：``POST /api/strategies/ma1/sync-pos?pos=5`` 表示此策略持有 5 手。"""
    try:
        return engine.sync_strategy_pos(name, pos)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/{name}")
def get_strategy(
    name: str,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    strategy = engine.strategies.get(name)
    if not strategy:
        raise HTTPException(404, f"strategy not found: {name}")
    return strategy.get_data()


@router.put("/{name}")
def edit_strategy(
    name: str,
    body: EditStrategyBody,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    try:
        engine.edit_strategy(name, body.setting)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    strategy = engine.strategies[name]
    return strategy.get_data()


@router.delete("/{name}", status_code=200)
def remove_strategy(
    name: str,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    try:
        engine.remove_strategy(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return {"removed": name}


@router.post("/{name}/init", status_code=202)
def init_strategy(
    name: str,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    if name not in engine.strategies:
        raise HTTPException(404, f"strategy not found: {name}")
    engine.init_strategy(name)
    return {"accepted": True, "name": name}


@router.post("/{name}/start")
def start_strategy(
    name: str,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    try:
        engine.start_strategy(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    return engine.strategies[name].get_data()


@router.post("/{name}/stop")
def stop_strategy(
    name: str,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    try:
        engine.stop_strategy(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    return engine.strategies[name].get_data()


@router.put("/{name}/auto-start")
def set_auto_start(
    name: str,
    body: dict,
    engine: StrategyEngine = Depends(get_strategy_engine),
) -> dict:
    enabled = body.get("enabled", False)
    try:
        engine.set_auto_start(name, bool(enabled))
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    return engine.strategies[name].get_data()
