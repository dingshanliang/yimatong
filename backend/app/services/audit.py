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
    catalog_actions = {
        "product_created",
        "product_updated",
        "sku_created",
        "sku_updated",
        "production_batch_created",
        "production_batch_updated",
    }
    if (
        db.get_bind().dialect.name == "postgresql"
        and credential is not None
        and credential[0] == "api_key"
        and action in catalog_actions
    ):
        try:
            resource_id = uuid.UUID(resource.rsplit(":", 1)[-1])
        except (ValueError, AttributeError) as exc:
            raise ValueError("Catalog audit resource must end with a UUID") from exc
        audit_id = uuid7()
        statement = text(
            "SELECT audit_id, resolved_operator_id, recorded_at "
            "FROM public.append_api_key_catalog_audit_event("
            ":audit_id, :api_key_id, :target_tenant_id, :action, :resource_id, :details)"
        ).bindparams(bindparam("details", type_=JSONB))
        row = (
            await db.execute(
                statement,
                {
                    "audit_id": audit_id,
                    "api_key_id": uuid.UUID(credential[1]),
                    "target_tenant_id": uuid.UUID(target_tenant_id),
                    "action": action,
                    "resource_id": resource_id,
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


async def query_audit_logs_page(
    db: AsyncSession,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """分页查询平台审计记录（含总数），保证超期历史可回看。"""
    conditions = []
    if start_time:
        conditions.append(PlatformAuditLog.timestamp >= start_time)
    if end_time:
        conditions.append(PlatformAuditLog.timestamp <= end_time)

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
    return {"items": logs, "total": total, "page": page, "page_size": page_size}


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
