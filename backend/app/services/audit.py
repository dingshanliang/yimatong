import uuid
from datetime import UTC, datetime

from sqlalchemy import bindparam, func, insert, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import get_request_security_credential
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
    # ``inline()`` disables PostgreSQL's implicit RETURNING. Append permission
    # is intentionally broader than read permission for an agency recording
    # entry to a client, and RETURNING would incorrectly require SELECT access
    # to that client's audit row. The statement still runs in the caller's
    # transaction, preserving business-write/audit atomicity.
    recorded_at = datetime.now(UTC)
    credential = get_request_security_credential()
    if db.get_bind().dialect.name == "postgresql" and credential is not None and credential[0] == "auth_session":
        audit_id = uuid7()
        statement = text(
            "SELECT audit_id, resolved_operator_id, recorded_at "
            "FROM public.append_authenticated_audit_event("
            ":audit_id, :session_id, :target_tenant_id, :action, :resource, :details)"
        ).bindparams(bindparam("details", type_=JSONB))
        row = (
            await db.execute(
                statement,
                {
                    "audit_id": audit_id,
                    "session_id": uuid.UUID(credential[1]),
                    "target_tenant_id": target_tenant_id,
                    "action": action,
                    "resource": resource,
                    "details": details,
                },
            )
        ).one()
        return PlatformAuditLog(
            id=row.audit_id,
            operator_id=row.resolved_operator_id,
            target_tenant_id=target_tenant_id,
            action=action,
            resource=resource,
            details=details,
            timestamp=row.recorded_at,
            created_at=row.recorded_at,
            updated_at=row.recorded_at,
        )

    log = PlatformAuditLog(
        id=uuid7(),
        operator_id=operator_id,
        target_tenant_id=target_tenant_id,
        action=action,
        resource=resource,
        details=details,
        timestamp=recorded_at,
        created_at=recorded_at,
        updated_at=recorded_at,
    )
    await db.execute(
        insert(PlatformAuditLog)
        .inline()
        .values(
            id=log.id,
            operator_id=log.operator_id,
            target_tenant_id=log.target_tenant_id,
            action=log.action,
            resource=log.resource,
            details=log.details,
            timestamp=log.timestamp,
            created_at=log.created_at,
            updated_at=log.updated_at,
        )
    )
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
