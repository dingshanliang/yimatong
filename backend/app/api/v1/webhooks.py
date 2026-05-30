"""Webhook / Open API 管理"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.webhook import (
    create_api_key,
    create_webhook_endpoint,
    delete_webhook_endpoint,
    list_api_keys,
    list_deliveries,
    list_webhook_endpoints,
    revoke_api_key,
    update_webhook_endpoint,
)

webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str
    events: list[str]
    description: str | None = None
    batch_mode: bool = False
    batch_size: int = 100


class WebhookUpdate(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    description: str | None = None
    enabled: bool | None = None
    batch_mode: bool | None = None
    batch_size: int | None = None


class ApiKeyCreate(BaseModel):
    name: str
    role: str = "data_reader"
    expires_at: datetime | None = None


@webhook_router.post("/endpoints", status_code=201)
async def create_endpoint(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    ep = await create_webhook_endpoint(
        db,
        tenant_id,
        body.url,
        body.events,
        description=body.description,
        batch_mode=body.batch_mode,
        batch_size=body.batch_size,
    )
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "description": ep.description,
        "enabled": ep.enabled,
        "batch_mode": ep.batch_mode,
        "batch_size": ep.batch_size,
        "secret": ep.secret,
    }


@webhook_router.get("/endpoints")
async def list_endpoints(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    eps = await list_webhook_endpoints(db, tenant_id)
    return [
        {
            "id": str(e.id),
            "url": e.url,
            "events": e.events,
            "description": e.description,
            "enabled": e.enabled,
            "batch_mode": e.batch_mode,
            "batch_size": e.batch_size,
        }
        for e in eps
    ]


@webhook_router.patch("/endpoints/{endpoint_id}")
async def update_endpoint(
    endpoint_id: uuid.UUID,
    body: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    ep = await update_webhook_endpoint(
        db,
        tenant_id,
        endpoint_id,
        url=body.url,
        events=body.events,
        description=body.description,
        enabled=body.enabled,
        batch_mode=body.batch_mode,
        batch_size=body.batch_size,
    )
    if not ep:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "description": ep.description,
        "enabled": ep.enabled,
        "batch_mode": ep.batch_mode,
        "batch_size": ep.batch_size,
    }


@webhook_router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deleted = await delete_webhook_endpoint(db, tenant_id, endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return {"deleted": True}


@webhook_router.post("/api-keys", status_code=201)
async def create_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        key = await create_api_key(db, tenant_id, body.name, role=body.role, expires_at=body.expires_at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "id": str(key.id),
        "name": key.name,
        "key": key.key,
        "role": key.role,
        "permissions": key.permissions,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    }


@webhook_router.get("/api-keys")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    keys = await list_api_keys(db, tenant_id)
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "role": k.role,
            "permissions": k.permissions,
            "revoked": k.revoked,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


@webhook_router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        await revoke_api_key(db, tenant_id, key_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"revoked": True}


@webhook_router.get("/deliveries")
async def list_deliveries_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deliveries, total = await list_deliveries(db, tenant_id, page=page, page_size=page_size, status=status)
    return PaginatedResponse(
        items=[
            {
                "id": str(d.id),
                "endpoint_id": str(d.endpoint_id),
                "event_id": d.event_id,
                "event_type": d.event_type,
                "status": d.status,
                "retry_count": d.retry_count,
                "last_response_code": d.last_response_code,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in deliveries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@webhook_router.get("/deliveries/{delivery_id}")
async def get_delivery_detail(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    from sqlalchemy import select

    from app.models.webhook import WebhookDelivery

    result = await db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.tenant_id == tenant_id,
        )
    )
    d = result.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return {
        "id": str(d.id),
        "endpoint_id": str(d.endpoint_id),
        "event_id": d.event_id,
        "event_type": d.event_type,
        "payload": d.payload,
        "status": d.status,
        "retry_count": d.retry_count,
        "next_retry_at": d.next_retry_at.isoformat() if d.next_retry_at else None,
        "last_response_code": d.last_response_code,
        "last_response_body": d.last_response_body,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
