"""配置备份 / 恢复：把策略、自选、网关配置、风控等打包为 ZIP 导出，
再整包导入恢复。

安全纪律（借鉴 ARL/TSP，fail-closed）：
- **密钥一律不导出**（CTP 密码 / fuyao API Key / 通知 secrets / JWT），
  导入时合并到现有配置、绝不覆盖密钥；
- 导入仅接受本平台导出的 manifest，逐条目校验；
- 条目名白名单校验，拒绝路径穿越；
- 策略导入按 name upsert（不删除目标库中已有但包里没有的策略）。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..config import DATA_DIR
from ..core.utility import CHINA_TZ

MANIFEST_NAME = "manifest.json"
APP_TAG = "custom-trader"
BACKUP_VERSION = 1

# 类别 → 文件相对路径（data/ 下）
FILE_CATEGORIES = {
    "watchlist": ["watchlist.json"],
    "gateway_configs": ["stock_config.json", "fuyao_config.json", "ctp_config.json", "sim_config.json"],
    "notify_config": ["notify_config.json"],
    "risk_settings": ["risk_config.json"],
}

# 每个文件中的敏感字段：导出时移除、导入时保留现值
SECRET_JSON_FIELDS = {
    "fuyao_config.json": ["api_key"],
    "ctp_config.json": ["password", "auth_code"],
    "notify_config.json": ["feishu_secret", "email_password"],
}


def _read_json(path) -> Any | None:  # noqa: ANN001
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _strip_secrets(filename: str, data: Any) -> Any:
    if isinstance(data, dict):
        for field in SECRET_JSON_FIELDS.get(filename, []):
            data.pop(field, None)
    return data


# ----------------------------------------------------------------------
# 策略（DB 表）
# ----------------------------------------------------------------------

def _dump_strategies() -> list[dict]:
    from ..db.models import StrategyModel
    from ..db.session import create_session

    session = create_session()
    try:
        rows = session.scalars(select(StrategyModel)).all()
        return [
            {
                "name": r.name,
                "class_name": r.class_name,
                "vt_symbols": r.vt_symbols,
                "setting": r.setting,
                "status": "STOPPED",  # 恢复后一律停止态
                "variables": r.variables,
                "auto_start": False,  # 备份不携带自动启动，防误拉起
            }
            for r in rows
        ]
    finally:
        session.close()


def _restore_strategies(rows: list[dict], allow_overwrite: bool) -> dict:
    from ..db.models import StrategyModel
    from ..db.session import create_session

    created, updated, skipped = 0, 0, 0
    session = create_session()
    try:
        existing = {r.name: r for r in session.scalars(select(StrategyModel)).all()}
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name or not row.get("class_name"):
                skipped += 1
                continue
            target = existing.get(name)
            if target is not None:
                if not allow_overwrite:
                    skipped += 1
                    continue
                target.class_name = row["class_name"]
                target.vt_symbols = str(row.get("vt_symbols") or "[]")
                target.setting = str(row.get("setting") or "{}")
                target.status = "STOPPED"
                target.variables = str(row.get("variables") or "{}")
                target.auto_start = False
                updated += 1
            else:
                session.add(
                    StrategyModel(
                        name=name,
                        class_name=row["class_name"],
                        vt_symbols=str(row.get("vt_symbols") or "[]"),
                        setting=str(row.get("setting") or "{}"),
                        status="STOPPED",
                        variables=str(row.get("variables") or "{}"),
                        auto_start=False,
                    )
                )
                created += 1
        session.commit()
    finally:
        session.close()
    return {"created": created, "updated": updated, "skipped": skipped}


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------

def export_backup(items: dict[str, bool]) -> tuple[str, bytes]:
    """按类别勾选导出 ZIP，返回 (文件名, 字节)。"""
    manifest: dict[str, Any] = {
        "app": APP_TAG,
        "version": BACKUP_VERSION,
        "created_at": datetime.now(CHINA_TZ).isoformat(),
        "items": {},
    }
    files: dict[str, Any] = {}

    if items.get("strategies", True):
        strategies = _dump_strategies()
        files["strategies.json"] = strategies
        manifest["items"]["strategies"] = len(strategies)

    for category, filenames in FILE_CATEGORIES.items():
        if not items.get(category, True):
            continue
        dumped = {}
        for filename in filenames:
            data = _read_json(DATA_DIR / filename)
            if data is not None:
                dumped[filename] = _strip_secrets(filename, data)
        if dumped:
            files[f"configs/{category}.json"] = dumped
            manifest["items"][category] = len(dumped)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, ensure_ascii=False, indent=2))
    timestamp = datetime.now(CHINA_TZ).strftime("%Y%m%d_%H%M%S")
    return f"custom-trader-backup-{timestamp}.zip", buf.getvalue()


# ----------------------------------------------------------------------
# 导入
# ----------------------------------------------------------------------

def import_backup(data: bytes, allow_overwrite: bool = False) -> dict:
    """从 ZIP 恢复。返回各类别处理摘要。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        # 防炸弹：条目数与总大小上限
        if len(names) > 100:
            raise ValueError(f"压缩包条目过多（{len(names)}），拒绝导入")
        total = sum(i.file_size for i in zf.infolist())
        if total > 64 * 1024 * 1024:
            raise ValueError("压缩包解压总量超过 64MB，拒绝导入")

        manifest = json.loads(zf.read(MANIFEST_NAME))
        if manifest.get("app") != APP_TAG:
            raise ValueError(f"不是 {APP_TAG} 的备份文件（manifest.app={manifest.get('app')!r}）")
        version = int(manifest.get("version", 0))
        if version > BACKUP_VERSION:
            raise ValueError(f"备份版本过新（{version} > {BACKUP_VERSION}），请先升级平台")

        summary: dict[str, Any] = {"created_at": manifest.get("created_at"), "restored": {}}

        # 1. 策略
        if "strategies.json" in names:
            rows = json.loads(zf.read("strategies.json"))
            if not isinstance(rows, list):
                raise ValueError("strategies.json 格式错误")
            summary["restored"]["strategies"] = _restore_strategies(rows, allow_overwrite)

        # 2. 配置文件（合并导入：备份里的值覆盖现值，但密钥保留现值）
        for name in names:
            if not name.startswith("configs/") or not name.endswith(".json"):
                continue
            category = name[len("configs/"):-len(".json")]
            bucket = json.loads(zf.read(name))
            if not isinstance(bucket, dict):
                continue
            merged = []
            for filename, data in bucket.items():
                # 白名单校验：只允许 FILE_CATEGORIES 中登记过的文件
                if filename not in FILE_CATEGORIES.get(category, []):
                    continue
                path = DATA_DIR / filename
                current = _read_json(path)
                # watchlist.json 等列表型配置：整文件替换（同构校验）
                if isinstance(data, list):
                    if current is not None and not isinstance(current, list):
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    merged.append(filename)
                    continue
                if not isinstance(data, dict):
                    continue
                if current is None or not isinstance(current, dict):
                    current = {}
                for field in SECRET_JSON_FIELDS.get(filename, []):
                    data.pop(field, None)  # 密钥绝不从备份恢复
                current.update(data)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(current, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                merged.append(filename)
            if merged:
                summary["restored"][category] = {"files": merged}

    return summary
