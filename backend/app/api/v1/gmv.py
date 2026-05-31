"""外部成交与 GMV 归因 API"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.gmv import (
    aggregate_daily_stats,
    batch_auto_attribution,
    get_gmv_dashboard,
    get_roi_report,
    import_orders,
    list_attributions,
    list_orders,
)

gmv_router = APIRouter(prefix="/api/v1/gmv", tags=["gmv"])


class OrderItem(BaseModel):
    external_id: str
    amount: float
    phone: str | None = None
    product_name: str | None = None
    order_time: str | None = None
    channel: str | None = None
    source_system: str | None = None


class OrderImportRequest(BaseModel):
    orders: list[OrderItem]


class AutoAttributionRequest(BaseModel):
    window_hours: int = 168
    limit: int = 500


@gmv_router.post("/orders/import", summary="导入订单")
async def import_orders_endpoint(
    body: OrderImportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orders_data = [o.model_dump() for o in body.orders]
    count = await import_orders(db, tenant_id, orders_data)
    return {"imported": count}


@gmv_router.get("/orders", summary="订单列表")
async def list_orders_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    matched: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orders, total = await list_orders(db, tenant_id, page=page, page_size=page_size, matched=matched)
    return PaginatedResponse(
        items=[
            {
                "id": str(o.id),
                "external_id": o.external_id,
                "amount": o.amount,
                "product_name": o.product_name,
                "matched": o.matched,
                "channel": o.channel,
                "source_system": o.source_system,
                "order_time": o.order_time.isoformat() if o.order_time else None,
            }
            for o in orders
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@gmv_router.post("/auto-attribution", summary="批量自动归因")
async def auto_attribution_endpoint(
    body: AutoAttributionRequest = AutoAttributionRequest(),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await batch_auto_attribution(db, tenant_id, window_hours=body.window_hours, limit=body.limit)


@gmv_router.get("/dashboard", summary="GMV 归因看板")
async def dashboard_endpoint(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_gmv_dashboard(
        db, tenant_id,
        start_date=start_date, end_date=end_date,
        campaign_id=campaign_id, channel=channel,
    )


@gmv_router.get("/roi", summary="ROI 报表")
async def roi_report_endpoint(
    campaign_id: uuid.UUID | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_roi_report(db, tenant_id, campaign_id=campaign_id, start_date=start_date, end_date=end_date)


@gmv_router.get("/attributions", summary="归因记录列表")
async def list_attributions_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    match_type: str | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    attrs, total = await list_attributions(
        db, tenant_id, page=page, page_size=page_size,
        match_type=match_type, campaign_id=campaign_id,
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(a.id),
                "external_order_id": str(a.external_order_id),
                "public_id": a.public_id,
                "code_item_id": str(a.code_item_id) if a.code_item_id else None,
                "campaign_id": str(a.campaign_id) if a.campaign_id else None,
                "consumer_id": str(a.consumer_id) if a.consumer_id else None,
                "amount": a.amount,
                "match_type": a.match_type,
                "scan_time": a.scan_time.isoformat() if a.scan_time else None,
                "confidence_score": a.confidence_score,
                "attribution_window_hours": a.attribution_window_hours,
            }
            for a in attrs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@gmv_router.post("/aggregate-daily", summary="手动触发日统计聚合")
async def aggregate_daily_endpoint(
    target_date: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    count = await aggregate_daily_stats(db, tenant_id, target_date=target_date)
    return {"aggregated": count}
