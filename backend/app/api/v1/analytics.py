"""统计 API"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.analytics import get_campaign_scan_stats, get_code_stats, get_dashboard, get_scan_stats
from app.services.analytics_extended import (
    get_alerts,
    get_campaign_ranking,
    get_conversion_funnel,
    get_recent_events,
)

analytics_router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@analytics_router.get("/scan-stats", summary="获取 scan stats")
async def get_scan_stats_endpoint(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_scan_stats(db, tenant_id, start_date, end_date)


@analytics_router.get("/code-stats", summary="获取 code stats")
async def get_code_stats_endpoint(
    code_batch_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_code_stats(db, tenant_id, code_batch_id)


@analytics_router.get("/dashboard", summary="获取 仪表盘")
async def get_dashboard_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_dashboard(db, tenant_id, days_back=days_back)


@analytics_router.get("/campaign-scan-stats", summary="获取活动维度扫码统计")
async def get_campaign_scan_stats_endpoint(
    campaign_id: uuid.UUID | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_campaign_scan_stats(db, tenant_id, campaign_id, start_date, end_date)


@analytics_router.get("/conversion-funnel", summary="获取转化漏斗数据")
async def get_conversion_funnel_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_conversion_funnel(db, tenant_id, days_back=days_back)


@analytics_router.get("/alerts", summary="获取状态提醒")
async def get_alerts_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_alerts(db, tenant_id)


@analytics_router.get("/campaign-ranking", summary="获取活动排行")
async def get_campaign_ranking_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_campaign_ranking(db, tenant_id, days_back=days_back, limit=limit)


@analytics_router.get("/recent-events", summary="获取最近动态")
async def get_recent_events_endpoint(
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_recent_events(db, tenant_id, limit=limit)
