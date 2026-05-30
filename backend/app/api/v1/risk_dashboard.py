"""渠道风控看板 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.risk_dashboard import (
    export_risk_data,
    get_cross_region_stats,
    get_diversion_summary,
    get_repeat_scan_stats,
)

risk_dashboard_router = APIRouter(prefix="/api/v1/risk-dashboard", tags=["risk-dashboard"])


def require_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")


@risk_dashboard_router.get("/repeat-scans")
async def repeat_scans_endpoint(
    min_count: int = Query(2, ge=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await get_repeat_scan_stats(db, tenant_id, min_count=min_count, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@risk_dashboard_router.get("/cross-region")
async def cross_region_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_cross_region_stats(db, tenant_id)


@risk_dashboard_router.get("/diversion-summary")
async def diversion_summary_endpoint(
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_diversion_summary(db, tenant_id, resolved=resolved, page=page, page_size=page_size)


@risk_dashboard_router.get("/export", response_class=PlainTextResponse)
async def export_endpoint(
    data_type: str = Query("alerts", pattern="^(alerts|diversions)$"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_admin),
):
    csv_data = await export_risk_data(db, tenant_id, data_type)

    from app.services.export_audit import log_export

    await log_export(
        db,
        tenant_id,
        account_id,
        f"risk_{data_type}_csv",
        file_name=f"risk_{data_type}.csv",
        row_count=csv_data.count("\n") - 1 if csv_data else 0,
    )
    await db.commit()

    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=risk_{data_type}.csv"},
    )
