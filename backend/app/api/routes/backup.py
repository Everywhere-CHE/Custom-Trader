"""配置备份端点：导出 ZIP / 导入恢复。"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response

from ...core.engine.main_engine import MainEngine
from ...services.config_backup import export_backup, import_backup
from ..deps import get_main_engine  # noqa: F401 —— 保持依赖面一致（认证已由中间件完成）
from ..schemas import ConfigBackupItem

router = APIRouter(prefix="/backup", tags=["backup"])


@router.post("/export")
def export_backup_endpoint(
    body: ConfigBackupItem,
    main_engine: MainEngine = Depends(get_main_engine),
) -> Response:
    """按类别勾选导出配置备份 ZIP（密钥一律不包含）。"""
    filename, blob = export_backup(body.model_dump())
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_backup_endpoint(
    file: UploadFile,
    allow_overwrite: bool = Query(False, description="同名策略是否覆盖（默认跳过）"),
    main_engine: MainEngine = Depends(get_main_engine),
) -> dict:
    """导入配置备份 ZIP。密钥不会被恢复（保留现值）。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(422, "请上传 .zip 备份文件")
    data = await file.read()
    if not data:
        raise HTTPException(422, "空文件")
    try:
        summary = import_backup(data, allow_overwrite=allow_overwrite)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except zipfile.BadZipFile as e:
        raise HTTPException(422, f"无效的 ZIP 文件: {e}") from None
    return summary
