"""渠道分析 API：按渠道扫码统计、健康评分、转化率对比"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.channel_analytics import (
    get_channel_health_scores,
    get_conversion_comparison,
    get_scan_by_channel,
)

channel_analytics_router = APIRouter(prefix="/api/v1/channel-analytics", tags=["channel-analytics"])


@channel_analytics_router.get("/scan-by-channel")
async def scan_by_channel_endpoint(
    dimension: str = Query("distributor", pattern="^(distributor|region|store)$"),
    days_back: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """按渠道维度聚合扫码统计"""
    items, total = await get_scan_by_channel(
        db, tenant_id, dimension=dimension, days_back=days_back, page=page, page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@channel_analytics_router.get("/health-scores")
async def health_scores_endpoint(
    dimension: str = Query("distributor", pattern="^(distributor|region|store)$"),
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """渠道健康评分"""
    scores = await get_channel_health_scores(db, tenant_id, dimension=dimension, days_back=days_back)
    return {"scores": scores}


@channel_analytics_router.get("/conversion-comparison")
async def conversion_comparison_endpoint(
    dimension: str = Query("distributor", pattern="^(distributor|region|store)$"),
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """渠道间转化率对比"""
    results = await get_conversion_comparison(db, tenant_id, dimension=dimension, days_back=days_back)
    return {"comparison": results}
