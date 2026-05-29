"""Webhook / Open API 管理"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.webhook import (
    create_api_key,
    create_webhook_endpoint,
    list_api_keys,
    list_deliveries,
    list_webhook_endpoints,
    revoke_api_key,
)

webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str
    events: list[str]
    secret: str


class ApiKeyCreate(BaseModel):
    name: str
    permissions: list[str] = []


@webhook_router.post("/endpoints", status_code=201)
async def create_endpoint(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    ep = await create_webhook_endpoint(db, tenant_id, body.url, body.events, body.secret)
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "enabled": ep.enabled,
    }


@webhook_router.get("/endpoints")
async def list_endpoints(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    eps = await list_webhook_endpoints(db, tenant_id)
    return [
        {"id": str(e.id), "url": e.url, "events": e.events, "enabled": e.enabled}
        for e in eps
    ]


@webhook_router.post("/api-keys", status_code=201)
async def create_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    key = await create_api_key(db, tenant_id, body.name, body.permissions)
    return {
        "id": str(key.id),
        "name": key.name,
        "key": key.key,
        "permissions": key.permissions,
    }


@webhook_router.get("/api-keys")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    keys = await list_api_keys(db, tenant_id)
    return [
        {"id": str(k.id), "name": k.name, "permissions": k.permissions, "revoked": k.revoked}
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deliveries, total = await list_deliveries(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[
            {
                "id": str(d.id),
                "endpoint_id": str(d.endpoint_id),
                "event_type": d.event_type,
                "status": d.status,
            }
            for d in deliveries
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
