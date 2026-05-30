"""导出审计日志服务"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.export_log import ExportLog


async def log_export(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    export_type: str,
    resource_id: str | None = None,
    file_name: str | None = None,
    row_count: int = 0,
) -> ExportLog:
    entry = ExportLog(
        tenant_id=tenant_id,
        account_id=account_id,
        export_type=export_type,
        resource_id=uuid.UUID(resource_id) if resource_id else None,
        file_name=file_name,
        row_count=row_count,
    )
    db.add(entry)
    await db.flush()
    return entry
