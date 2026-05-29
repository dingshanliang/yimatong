"""Webhook / Open API 服务"""

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import ApiKey, WebhookDelivery, WebhookEndpoint


async def create_webhook_endpoint(
    db: AsyncSession, tenant_id: uuid.UUID, url: str, events: list[str], secret: str,
) -> WebhookEndpoint:
    ep = WebhookEndpoint(tenant_id=tenant_id, url=url, events=events, secret=secret)
    db.add(ep)
    await db.flush()
    await db.refresh(ep)
    return ep


async def list_webhook_endpoints(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> list[WebhookEndpoint]:
    result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id).order_by(WebhookEndpoint.id.desc())
    )
    return list(result.scalars().all())


async def create_api_key(
    db: AsyncSession, tenant_id: uuid.UUID, name: str, permissions: list[str],
) -> ApiKey:
    key_str = f"ymt_{secrets.token_hex(24)}"
    api_key = ApiKey(
        tenant_id=tenant_id,
        name=name,
        key=key_str,
        permissions=permissions,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return api_key


async def list_api_keys(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.tenant_id == tenant_id, ApiKey.revoked.is_(False)).order_by(ApiKey.id.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(
    db: AsyncSession, tenant_id: uuid.UUID, key_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
    )
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("API key not found")
    key.revoked = True
    await db.flush()
    return True


async def list_deliveries(
    db: AsyncSession, tenant_id: uuid.UUID, page: int = 1, page_size: int = 20,
) -> tuple[list[WebhookDelivery], int]:
    count_stmt = select(func.count()).select_from(WebhookDelivery).where(
        WebhookDelivery.tenant_id == tenant_id,
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.tenant_id == tenant_id)
        .order_by(WebhookDelivery.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
