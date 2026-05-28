"""外部权益连接器 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.connector import (
    create_connector,
    create_coupon_pool,
    distribute_coupon,
    get_connector,
    list_connectors,
    test_connection,
    update_connector,
)

connector_router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


class CouponPoolCreate(BaseModel):
    name: str
    codes: list[str]


class DistributeRequest(BaseModel):
    consumer_id: str


class ConnectorCreate(BaseModel):
    name: str
    connector_type: str
    config: dict


class ConnectorUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    enabled: bool | None = None


@connector_router.post("/coupon-pools", status_code=201)
async def create_pool_endpoint(
    body: CouponPoolCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    pool = await create_coupon_pool(db, tenant_id, body.name, body.codes)
    return {
        "id": str(pool.id),
        "name": pool.name,
        "total_codes": pool.total_codes,
        "remaining": pool.remaining,
    }


@connector_router.post("/coupon-pools/{pool_id}/distribute")
async def distribute_endpoint(
    pool_id: uuid.UUID,
    body: DistributeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    code = await distribute_coupon(db, pool_id, body.consumer_id)
    if not code:
        raise HTTPException(status_code=400, detail="No available codes in pool")
    return {
        "code": code.code,
        "consumer_id": code.consumer_id,
        "distributed": True,
    }


@connector_router.post("/connectors", status_code=201)
async def create_connector_endpoint(
    body: ConnectorCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    conn = await create_connector(db, tenant_id, body.name, body.connector_type, body.config)
    return {
        "id": str(conn.id),
        "name": conn.name,
        "connector_type": conn.connector_type,
        "config": conn.config,
        "enabled": conn.enabled,
    }


@connector_router.get("/connectors")
async def list_connectors_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    conns = await list_connectors(db, tenant_id)
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "connector_type": c.connector_type,
            "config": c.config,
            "enabled": c.enabled,
        }
        for c in conns
    ]


@connector_router.post("/connectors/{conn_id}/test")
async def test_connection_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    conn = await get_connector(db, tenant_id, conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connector not found")
    return await test_connection(conn)


@connector_router.patch("/connectors/{conn_id}")
async def update_connector_endpoint(
    conn_id: uuid.UUID,
    body: ConnectorUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        conn = await update_connector(db, tenant_id, conn_id, **updates)
    except ValueError:
        raise HTTPException(status_code=404, detail="Connector not found")
    return {
        "id": str(conn.id),
        "name": conn.name,
        "connector_type": conn.connector_type,
        "config": conn.config,
        "enabled": conn.enabled,
    }
