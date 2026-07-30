"""Agency 授权管理服务"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import (
    AgencyAuthorization,
    AgencyAuthStatus,
    Tenant,
)


async def list_authorizations_for_agency(
    db: AsyncSession, agency_tenant_id: uuid.UUID, page: int = 1, page_size: int = 50
) -> tuple[list[dict], int]:
    """Agency 查看自己被授权的所有品牌客户（分页）。"""
    base_query = (
        select(AgencyAuthorization, Tenant.name, Tenant.slug)
        .join(Tenant, AgencyAuthorization.client_tenant_id == Tenant.id)
        .where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
        .order_by(AgencyAuthorization.granted_at.desc())
    )
    # Count
    from sqlalchemy import func

    count_result = await db.execute(
        select(func.count())
        .select_from(AgencyAuthorization)
        .where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
    )
    total = count_result.scalar() or 0
    # Paginate
    offset = (page - 1) * page_size
    result = await db.execute(base_query.offset(offset).limit(page_size))
    rows = result.all()
    items = [
        {
            "id": str(auth.id),
            "agency_tenant_id": str(auth.agency_tenant_id),
            "client_tenant_id": str(auth.client_tenant_id),
            "client_name": tenant_name,
            "client_slug": tenant_slug,
            "scope": auth.scope,
            "status": auth.status.value,
            "granted_at": auth.granted_at.isoformat() if auth.granted_at else None,
            "expires_at": auth.expires_at.isoformat() if auth.expires_at else None,
        }
        for auth, tenant_name, tenant_slug in rows
    ]
    return items, total


async def list_authorizations_for_brand(
    db: AsyncSession, client_tenant_id: uuid.UUID, page: int = 1, page_size: int = 50
) -> tuple[list[dict], int]:
    """Brand 查看哪些 agency 被授权访问自己（分页）。"""
    base_query = (
        select(AgencyAuthorization, Tenant.name)
        .join(Tenant, AgencyAuthorization.agency_tenant_id == Tenant.id)
        .where(
            AgencyAuthorization.client_tenant_id == client_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
        .order_by(AgencyAuthorization.granted_at.desc())
    )
    from sqlalchemy import func

    count_result = await db.execute(
        select(func.count())
        .select_from(AgencyAuthorization)
        .where(
            AgencyAuthorization.client_tenant_id == client_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
    )
    total = count_result.scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(base_query.offset(offset).limit(page_size))
    rows = result.all()
    items = [
        {
            "id": str(auth.id),
            "agency_tenant_id": str(auth.agency_tenant_id),
            "client_tenant_id": str(auth.client_tenant_id),
            "agency_name": agency_name,
            "scope": auth.scope,
            "status": auth.status.value,
            "granted_by": str(auth.granted_by) if auth.granted_by else None,
            "granted_at": auth.granted_at.isoformat() if auth.granted_at else None,
        }
        for auth, agency_name in rows
    ]
    return items, total


async def authorize_agency(
    db: AsyncSession,
    agency_tenant_id: uuid.UUID,
    client_tenant_id: uuid.UUID,
    scope: list[str],
    granted_by: uuid.UUID,
) -> AgencyAuthorization:
    """Brand 授权 agency 访问"""
    # Check for existing active authorization
    existing = await db.execute(
        select(AgencyAuthorization).where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.client_tenant_id == client_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
    )
    auth = existing.scalar_one_or_none()
    if auth:
        auth.scope = scope
        await db.flush()
        return auth

    auth = AgencyAuthorization(
        agency_tenant_id=agency_tenant_id,
        client_tenant_id=client_tenant_id,
        scope=scope,
        status=AgencyAuthStatus.active,
        granted_by=granted_by,
    )
    db.add(auth)
    await db.flush()
    return auth


async def revoke_authorization(
    db: AsyncSession, auth_id: uuid.UUID, client_tenant_id: uuid.UUID | None = None
) -> AgencyAuthorization | None:
    """撤销授权。若提供 client_tenant_id，则同时验证归属。"""
    result = await db.execute(select(AgencyAuthorization).where(AgencyAuthorization.id == auth_id))
    auth = result.scalar_one_or_none()
    if not auth:
        return None
    if auth.status != AgencyAuthStatus.active:
        return None
    # 归属校验：确保只能撤销属于自己的授权
    if client_tenant_id and auth.client_tenant_id != client_tenant_id:
        return None
    auth.status = AgencyAuthStatus.revoked
    auth.revoked_at = datetime.now(UTC)
    await db.flush()
    return auth


async def verify_authorization(
    db: AsyncSession,
    agency_tenant_id: uuid.UUID,
    client_tenant_id: uuid.UUID,
    required_scope: str | None = None,
) -> AgencyAuthorization | None:
    """验证 agency 是否有权限访问 brand，可选检查 scope"""
    result = await db.execute(
        select(AgencyAuthorization).where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.client_tenant_id == client_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
    )
    auth = result.scalar_one_or_none()
    if not auth:
        return None
    if required_scope and required_scope not in auth.scope:
        return None
    return auth


async def get_authorized_client_ids(db: AsyncSession, agency_tenant_id: uuid.UUID) -> list[uuid.UUID]:
    """获取 agency 被授权的所有 brand tenant id 列表"""
    result = await db.execute(
        select(AgencyAuthorization.client_tenant_id).where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
    )
    return [row[0] for row in result.all()]
