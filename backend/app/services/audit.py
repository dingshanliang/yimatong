from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import PlatformAuditLog


async def write_audit_log(
    db: AsyncSession,
    operator_id: str,
    target_tenant_id: str,
    action: str,
    resource: str,
) -> PlatformAuditLog:
    log = PlatformAuditLog(
        operator_id=operator_id,
        target_tenant_id=target_tenant_id,
        action=action,
        resource=resource,
    )
    db.add(log)
    await db.flush()
    await db.refresh(log)
    return log


async def query_audit_logs(
    db: AsyncSession,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 100,
) -> list[PlatformAuditLog]:
    stmt = select(PlatformAuditLog).order_by(PlatformAuditLog.timestamp.desc())
    if start_time:
        stmt = stmt.where(PlatformAuditLog.timestamp >= start_time)
    if end_time:
        stmt = stmt.where(PlatformAuditLog.timestamp <= end_time)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
