"""外部成交与 GMV 归因 API"""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.gmv import (
    get_gmv_dashboard,
    import_orders,
    list_attributions,
    list_orders,
    match_order,
)

gmv_router = APIRouter(prefix="/api/v1/gmv", tags=["gmv"])


class OrderItem(BaseModel):
    external_id: str
    amount: float
    phone: str | None = None
    product_name: str | None = None
    order_time: str | None = None


class OrderImportRequest(BaseModel):
    orders: list[OrderItem]


class MatchRequest(BaseModel):
    match_by: str
    value: str


@gmv_router.post("/orders/import")
async def import_orders_endpoint(
    body: OrderImportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orders_data = [o.model_dump() for o in body.orders]
    count = await import_orders(db, tenant_id, orders_data)
    return {"imported": count}


@gmv_router.get("/orders")
async def list_orders_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orders, total = await list_orders(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[
            {
                "id": str(o.id),
                "external_id": o.external_id,
                "amount": o.amount,
                "product_name": o.product_name,
                "matched": o.matched,
            }
            for o in orders
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@gmv_router.post("/match")
async def match_order_endpoint(
    body: MatchRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await match_order(db, tenant_id, body.match_by, body.value)


@gmv_router.get("/dashboard")
async def dashboard_endpoint(
    group_by: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_gmv_dashboard(db, tenant_id, group_by=group_by)


@gmv_router.get("/attributions")
async def list_attributions_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    attrs, total = await list_attributions(db, tenant_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[
            {
                "id": str(a.id),
                "external_order_id": str(a.external_order_id),
                "public_id": a.public_id,
                "amount": a.amount,
                "match_type": a.match_type,
            }
            for a in attrs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
