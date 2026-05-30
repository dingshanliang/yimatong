"""Webhook / Open API 服务"""

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import VALID_ROLES, get_permissions_for_role
from app.models.webhook import ApiKey, WebhookDelivery, WebhookEndpoint


def _generate_secret() -> str:
    """生成 webhook secret（whsec_ 前缀，32 字节随机）。"""
    return f"whsec_{secrets.token_hex(32)}"


async def create_webhook_endpoint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    url: str,
    events: list[str],
    description: str | None = None,
    batch_mode: bool = False,
    batch_size: int = 100,
) -> WebhookEndpoint:
    secret = _generate_secret()
    ep = WebhookEndpoint(
        tenant_id=tenant_id,
        url=url,
        events=events,
        secret=secret,
        description=description,
        batch_mode=batch_mode,
        batch_size=batch_size,
    )
    db.add(ep)
    await db.flush()
    await db.refresh(ep)
    return ep


async def update_webhook_endpoint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    *,
    url: str | None = None,
    events: list[str] | None = None,
    description: str | None = None,
    enabled: bool | None = None,
    batch_mode: bool | None = None,
    batch_size: int | None = None,
) -> WebhookEndpoint | None:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
    )
    ep = result.scalar_one_or_none()
    if not ep:
        return None
    if url is not None:
        ep.url = url
    if events is not None:
        ep.events = events
    if description is not None:
        ep.description = description
    if enabled is not None:
        ep.enabled = enabled
    if batch_mode is not None:
        ep.batch_mode = batch_mode
    if batch_size is not None:
        ep.batch_size = batch_size
    await db.flush()
    await db.refresh(ep)
    return ep


async def delete_webhook_endpoint(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    endpoint_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id,
            WebhookEndpoint.tenant_id == tenant_id,
        )
    )
    ep = result.scalar_one_or_none()
    if not ep:
        return False
    await db.delete(ep)
    await db.flush()
    return True


async def list_webhook_endpoints(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[WebhookEndpoint]:
    result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id).order_by(WebhookEndpoint.id.desc())
    )
    return list(result.scalars().all())


async def create_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    role: str = "data_reader",
    expires_at=None,
) -> ApiKey:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")
    permissions = get_permissions_for_role(role)
    key_str = f"ymt_{secrets.token_hex(24)}"
    api_key = ApiKey(
        tenant_id=tenant_id,
        name=name,
        key=key_str,
        role=role,
        permissions=permissions,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return api_key


async def list_api_keys(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.tenant_id == tenant_id, ApiKey.revoked.is_(False)).order_by(ApiKey.id.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> bool:
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id))
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("API key not found")
    key.revoked = True
    await db.flush()
    return True


async def list_deliveries(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> tuple[list[WebhookDelivery], int]:
    filters = [WebhookDelivery.tenant_id == tenant_id]
    if status:
        filters.append(WebhookDelivery.status == status)

    count_stmt = select(func.count()).select_from(WebhookDelivery).where(*filters)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        select(WebhookDelivery)
        .where(*filters)
        .order_by(WebhookDelivery.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
