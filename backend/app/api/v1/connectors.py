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
from app.services.external_benefit import (
    ExternalBenefitError,
    deliver_benefit,
    delivery_callback,
    get_pending_retries,
    retry_delivery,
    sync_stock,
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


class DeliverBenefitRequest(BaseModel):
    consumer_id: str
    benefit_type: str
    benefit_config: dict = {}


class DeliveryCallbackRequest(BaseModel):
    status: str
    external_data: dict = {}


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


# --- L2-L4: 外部权益连接器扩展 ---


@connector_router.post("/connectors/{conn_id}/sync-stock")
async def sync_stock_endpoint(
    conn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """L2: 从外部系统同步库存"""
    try:
        return await sync_stock(db, tenant_id, conn_id)
    except ExternalBenefitError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@connector_router.post("/connectors/{conn_id}/deliver")
async def deliver_benefit_endpoint(
    conn_id: uuid.UUID,
    body: DeliverBenefitRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """L3: 向外部系统发放权益"""
    conn = await get_connector(db, tenant_id, conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connector not found")
    try:
        return await deliver_benefit(
            db,
            tenant_id,
            conn,
            body.consumer_id,
            benefit_type=body.benefit_type,
            benefit_config=body.benefit_config,
        )
    except ExternalBenefitError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@connector_router.post("/deliveries/{delivery_id}/callback")
async def delivery_callback_endpoint(
    delivery_id: uuid.UUID,
    body: DeliveryCallbackRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """L3: 接收外部系统的发放结果回调"""
    try:
        return await delivery_callback(db, tenant_id, delivery_id, body.status, body.external_data)
    except ExternalBenefitError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@connector_router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery_endpoint(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """L4: 重试失败的权益发放"""
    try:
        return await retry_delivery(db, delivery_id, tenant_id)
    except ExternalBenefitError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@connector_router.get("/deliveries/pending-retries")
async def pending_retries_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """L4: 获取待重试的发放记录"""
    deliveries = await get_pending_retries(db, tenant_id)
    return [
        {
            "id": str(d.id),
            "connector_id": str(d.connector_id),
            "consumer_id": d.consumer_id,
            "benefit_type": d.benefit_type,
            "status": d.status,
            "retry_count": d.retry_count,
            "max_retries": d.max_retries,
            "next_retry_at": d.next_retry_at.isoformat() if d.next_retry_at else None,
        }
        for d in deliveries
    ]
