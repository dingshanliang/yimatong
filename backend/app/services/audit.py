import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import PlatformAuditLog
from app.models.tenant import Account
from app.utils import escape_like_pattern


async def write_audit_log(
    db: AsyncSession,
    operator_id: str,
    target_tenant_id: str,
    action: str,
    resource: str,
    details: dict | None = None,
) -> PlatformAuditLog:
    log = PlatformAuditLog(
        operator_id=operator_id,
        target_tenant_id=target_tenant_id,
        action=action,
        resource=resource,
        details=details,
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


async def query_tenant_audit_logs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    action: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """查询单个租户的审计记录，并补全可读的操作人信息。"""
    tenant_key = str(tenant_id)
    conditions = [
        PlatformAuditLog.target_tenant_id == tenant_key,
        PlatformAuditLog.operator_id != "platform-admin",
    ]

    if start_time:
        conditions.append(PlatformAuditLog.timestamp >= start_time)
    if end_time:
        conditions.append(PlatformAuditLog.timestamp <= end_time)
    if action:
        conditions.append(PlatformAuditLog.action == action)
    if keyword:
        escaped = escape_like_pattern(keyword)
        matching_actor_ids = [
            str(account_id)
            for account_id in (
                await db.execute(
                    select(Account.id).where(
                        Account.tenant_id == tenant_id,
                        or_(
                            Account.name.ilike(f"%{escaped}%", escape="\\"),
                            Account.email.ilike(f"%{escaped}%", escape="\\"),
                        ),
                    )
                )
            ).scalars()
        ]
        keyword_conditions = [
            PlatformAuditLog.action.ilike(f"%{escaped}%", escape="\\"),
            PlatformAuditLog.resource.ilike(f"%{escaped}%", escape="\\"),
            PlatformAuditLog.details["resource_name"].as_string().ilike(f"%{escaped}%", escape="\\"),
            PlatformAuditLog.details["reason"].as_string().ilike(f"%{escaped}%", escape="\\"),
        ]
        if matching_actor_ids:
            keyword_conditions.append(PlatformAuditLog.operator_id.in_(matching_actor_ids))
        conditions.append(or_(*keyword_conditions))

    total = (await db.execute(select(func.count()).select_from(PlatformAuditLog).where(*conditions))).scalar_one()
    logs = list(
        (
            await db.execute(
                select(PlatformAuditLog)
                .where(*conditions)
                .order_by(PlatformAuditLog.timestamp.desc(), PlatformAuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    actor_ids: list[uuid.UUID] = []
    for log in logs:
        try:
            actor_ids.append(uuid.UUID(log.operator_id))
        except ValueError:
            continue
    actors = {
        str(account.id): account
        for account in (
            (
                await db.execute(
                    select(Account).where(
                        Account.tenant_id == tenant_id,
                        Account.id.in_(actor_ids),
                    )
                )
            )
            .scalars()
            .all()
            if actor_ids
            else []
        )
    }
    return {"items": logs, "actors": actors, "total": total, "page": page, "page_size": page_size}
