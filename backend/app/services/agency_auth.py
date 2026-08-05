"""Agency 授权管理服务"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.database import _is_pg
from app.models.tenant import (
    AgencyAuthorization,
    AgencyAuthScope,
    AgencyAuthStatus,
    Tenant,
    TenantStatus,
    TenantType,
)


async def list_authorizations_for_agency(
    db: AsyncSession, agency_tenant_id: uuid.UUID, page: int = 1, page_size: int = 50
) -> tuple[list[dict], int]:
    """Agency 查看自己被授权的所有品牌客户（分页）。"""
    base_query = (
        select(AgencyAuthorization)
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
    authorizations = list(result.scalars().all())
    tenant_labels = await _load_tenant_labels(db, {auth.client_tenant_id for auth in authorizations})
    items = [
        {
            "id": str(auth.id),
            "agency_tenant_id": str(auth.agency_tenant_id),
            "client_tenant_id": str(auth.client_tenant_id),
            "client_name": tenant_labels.get(auth.client_tenant_id, (None, None))[0],
            "client_slug": tenant_labels.get(auth.client_tenant_id, (None, None))[1],
            "scope": auth.scope,
            "status": auth.status.value,
            "granted_at": auth.granted_at.isoformat() if auth.granted_at else None,
            "expires_at": auth.expires_at.isoformat() if auth.expires_at else None,
        }
        for auth in authorizations
    ]
    return items, total


async def list_authorizations_for_brand(
    db: AsyncSession, client_tenant_id: uuid.UUID, page: int = 1, page_size: int = 50
) -> tuple[list[dict], int]:
    """Brand 查看哪些 agency 被授权访问自己（分页）。"""
    base_query = (
        select(AgencyAuthorization)
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
    authorizations = list(result.scalars().all())
    tenant_labels = await _load_tenant_labels(db, {auth.agency_tenant_id for auth in authorizations})
    items = [
        {
            "id": str(auth.id),
            "agency_tenant_id": str(auth.agency_tenant_id),
            "client_tenant_id": str(auth.client_tenant_id),
            "agency_name": tenant_labels.get(auth.agency_tenant_id, (None, None))[0],
            "scope": auth.scope,
            "status": auth.status.value,
            "granted_by": str(auth.granted_by) if auth.granted_by else None,
            "granted_at": auth.granted_at.isoformat() if auth.granted_at else None,
        }
        for auth in authorizations
    ]
    return items, total


async def _load_tenant_labels(db: AsyncSession, tenant_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str]]:
    """读取授权关系明确指向的对方租户最小展示字段，不放宽 tenants RLS。"""
    if not tenant_ids:
        return {}
    if not _is_pg:
        rows = (await db.execute(select(Tenant.id, Tenant.name, Tenant.slug).where(Tenant.id.in_(tenant_ids)))).all()
    else:
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            rows = (
                await control_db.execute(select(Tenant.id, Tenant.name, Tenant.slug).where(Tenant.id.in_(tenant_ids)))
            ).all()
    return {tenant_id: (name, slug) for tenant_id, name, slug in rows}


async def authorize_agency(
    db: AsyncSession,
    agency_tenant_id: uuid.UUID,
    client_tenant_id: uuid.UUID,
    scope: list[str],
    granted_by: uuid.UUID,
) -> AgencyAuthorization:
    """Brand 授权 agency 访问"""
    valid_scopes = {item.value for item in AgencyAuthScope}
    normalized_scope = list(dict.fromkeys(scope))
    if not normalized_scope or any(item not in valid_scopes for item in normalized_scope):
        raise ValueError("授权范围无效")
    if _is_pg:
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            agency = (
                await control_db.execute(select(Tenant).where(Tenant.id == agency_tenant_id))
            ).scalar_one_or_none()
    else:
        agency = (await db.execute(select(Tenant).where(Tenant.id == agency_tenant_id))).scalar_one_or_none()
    if (
        agency is None
        or agency.tenant_type != TenantType.agency
        or agency.status != TenantStatus.active
        or agency.id == client_tenant_id
    ):
        raise ValueError("代运营租户无效或不可授权")
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize one logical agency/client authorization pair inside the
        # caller's transaction. The partial unique index remains the final
        # integrity guard, while this lock makes concurrent retries return the
        # same active authorization instead of surfacing an IntegrityError.
        pair_key = f"{agency_tenant_id}:{client_tenant_id}"
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:pair_key, 0))"),
            {"pair_key": pair_key},
        )
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
        auth.scope = normalized_scope
        await db.flush()
        return auth

    auth = AgencyAuthorization(
        agency_tenant_id=agency_tenant_id,
        client_tenant_id=client_tenant_id,
        scope=normalized_scope,
        status=AgencyAuthStatus.active,
        granted_by=granted_by,
    )
    db.add(auth)
    await db.flush()
    return auth


async def resolve_active_agency_by_slug(db: AsyncSession, slug: str) -> Tenant | None:
    """Resolve one business-readable agency identifier through the controlled tenant boundary."""
    query = select(Tenant).where(
        Tenant.slug == slug,
        Tenant.tenant_type == TenantType.agency,
        Tenant.status == TenantStatus.active,
    )
    if not _is_pg:
        return (await db.execute(query)).scalar_one_or_none()
    from app.core.database import control_session_factory

    async with control_session_factory() as control_db:
        await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        return (await control_db.execute(query)).scalar_one_or_none()


async def revoke_authorization(
    db: AsyncSession, auth_id: uuid.UUID, client_tenant_id: uuid.UUID | None = None
) -> AgencyAuthorization | None:
    """撤销授权。若提供 client_tenant_id，则同时验证归属。"""
    result = await db.execute(select(AgencyAuthorization).where(AgencyAuthorization.id == auth_id))
    auth = result.scalar_one_or_none()
    if not auth:
        return None
    expires_at = auth.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
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
    if _is_pg:
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            return await _verify_authorization_in_session(
                control_db, agency_tenant_id, client_tenant_id, required_scope
            )
    return await _verify_authorization_in_session(db, agency_tenant_id, client_tenant_id, required_scope)


async def _verify_authorization_in_session(
    db: AsyncSession,
    agency_tenant_id: uuid.UUID,
    client_tenant_id: uuid.UUID,
    required_scope: str | None,
) -> AgencyAuthorization | None:
    agency = aliased(Tenant)
    client = aliased(Tenant)
    result = await db.execute(
        select(AgencyAuthorization)
        .join(agency, agency.id == AgencyAuthorization.agency_tenant_id)
        .join(client, client.id == AgencyAuthorization.client_tenant_id)
        .where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.client_tenant_id == client_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
            agency.status == TenantStatus.active,
            client.status == TenantStatus.active,
        )
    )
    auth = result.scalar_one_or_none()
    if not auth:
        return None
    expires_at = auth.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
    if required_scope and required_scope not in auth.scope:
        return None
    return auth


async def get_authorized_client_ids(
    db: AsyncSession,
    agency_tenant_id: uuid.UUID,
    required_scopes: set[str] | None = None,
    *,
    match_all_scopes: bool = True,
    _controlled: bool = False,
) -> list[uuid.UUID]:
    """获取 agency 当前有效且满足 scope 的 brand tenant id 列表。"""
    if db.get_bind().dialect.name == "postgresql" and not _controlled:
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            return await get_authorized_client_ids(
                control_db,
                agency_tenant_id,
                required_scopes,
                match_all_scopes=match_all_scopes,
                _controlled=True,
            )
    agency = aliased(Tenant)
    client = aliased(Tenant)
    result = await db.execute(
        select(AgencyAuthorization)
        .join(agency, agency.id == AgencyAuthorization.agency_tenant_id)
        .join(client, client.id == AgencyAuthorization.client_tenant_id)
        .where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.status == AgencyAuthStatus.active,
            agency.status == TenantStatus.active,
            client.status == TenantStatus.active,
        )
    )
    now = datetime.now(UTC)
    client_ids: list[uuid.UUID] = []
    for authorization in result.scalars():
        expires_at = authorization.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                continue
        live_scopes = set(authorization.scope)
        if required_scopes and (
            not required_scopes.issubset(live_scopes)
            if match_all_scopes
            else not required_scopes.intersection(live_scopes)
        ):
            continue
        client_ids.append(authorization.client_tenant_id)
    return client_ids


async def get_authorized_client_scopes(
    db: AsyncSession,
    agency_tenant_id: uuid.UUID,
    visible_scopes: set[str],
    *,
    _controlled: bool = False,
) -> dict[uuid.UUID, set[str]]:
    if db.get_bind().dialect.name == "postgresql" and not _controlled:
        from app.core.database import control_session_factory

        async with control_session_factory() as control_db:
            await control_db.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            return await get_authorized_client_scopes(
                control_db,
                agency_tenant_id,
                visible_scopes,
                _controlled=True,
            )
    client_ids = await get_authorized_client_ids(
        db,
        agency_tenant_id,
        visible_scopes,
        match_all_scopes=False,
        _controlled=_controlled,
    )
    if not client_ids:
        return {}
    result = await db.execute(
        select(AgencyAuthorization.client_tenant_id, AgencyAuthorization.scope).where(
            AgencyAuthorization.agency_tenant_id == agency_tenant_id,
            AgencyAuthorization.client_tenant_id.in_(client_ids),
            AgencyAuthorization.status == AgencyAuthStatus.active,
        )
    )
    return {client_id: set(scopes).intersection(visible_scopes) for client_id, scopes in result.all()}
