"""统计 API"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.analytics import get_code_stats, get_dashboard, get_scan_stats

analytics_router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@analytics_router.get("/scan-stats")
async def get_scan_stats_endpoint(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_scan_stats(db, tenant_id, start_date, end_date)


@analytics_router.get("/code-stats")
async def get_code_stats_endpoint(
    code_batch_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_code_stats(db, tenant_id, code_batch_id)


@analytics_router.get("/dashboard")
async def get_dashboard_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_dashboard(db, tenant_id, days_back=days_back)
