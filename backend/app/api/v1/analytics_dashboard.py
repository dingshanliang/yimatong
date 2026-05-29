"""数据看板 API（活动看板、风控看板、区域看板、导出）"""

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.campaign import BenefitClaim, Campaign
from app.models.scan import ScanEvent

dashboard_router = APIRouter(prefix="/api/v1/analytics", tags=["analytics-dashboards"])


@dashboard_router.get("/campaign-dashboard")
async def campaign_dashboard(
    campaign_id: uuid.UUID | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """活动看板数据"""
    # 活动列表 + 基础统计
    campaign_stmt = select(Campaign).where(Campaign.tenant_id == tenant_id)
    if campaign_id:
        campaign_stmt = campaign_stmt.where(Campaign.id == campaign_id)
    result = await db.execute(campaign_stmt)
    campaigns = result.scalars().all()

    items = []
    for c in campaigns:
        # 每个活动的领取数
        claim_count_stmt = select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.campaign_id == c.id,
        )
        claim_result = await db.execute(claim_count_stmt)
        claim_count = claim_result.scalar() or 0

        # 每个活动的扫码数
        scan_count_stmt = select(func.count()).select_from(ScanEvent).where(
            ScanEvent.tenant_id == tenant_id,
        )
        scan_result = await db.execute(scan_count_stmt)
        scan_count = scan_result.scalar() or 0

        items.append({
            "campaign_id": str(c.id),
            "campaign_name": c.name,
            "status": c.status,
            "claim_count": claim_count,
            "scan_count": scan_count,
        })

    return {"items": items, "total": len(items)}


@dashboard_router.get("/risk-dashboard")
async def risk_dashboard(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """风控看板数据"""
    from app.models.risk import RiskAlert

    # 按类型统计预警
    type_stats_stmt = (
        select(RiskAlert.alert_type, func.count())
        .where(RiskAlert.tenant_id == tenant_id)
        .group_by(RiskAlert.alert_type)
    )
    type_result = await db.execute(type_stats_stmt)
    type_stats = {str(t): c for t, c in type_result.all()}

    # 未解决预警数
    unresolved_stmt = select(func.count()).select_from(RiskAlert).where(
        RiskAlert.tenant_id == tenant_id,
        not RiskAlert.resolved,
    )
    unresolved_result = await db.execute(unresolved_stmt)
    unresolved_count = unresolved_result.scalar() or 0

    return {
        "type_stats": type_stats,
        "unresolved_count": unresolved_count,
        "total_alerts": sum(type_stats.values()),
    }


@dashboard_router.get("/regional-dashboard")
async def regional_dashboard(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """区域看板数据"""
    from app.models.channel import DiversionClue

    # 窜货线索统计
    clue_stats_stmt = select(
        DiversionClue.resolved, func.count()
    ).where(
        DiversionClue.tenant_id == tenant_id
    ).group_by(DiversionClue.resolved)
    clue_result = await db.execute(clue_stats_stmt)
    clue_stats = {str(resolved): count for resolved, count in clue_result.all()}

    return {
        "diversion_stats": clue_stats,
        "total_clues": sum(clue_stats.values()),
    }


@dashboard_router.get("/exports")
async def list_exports(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """导出记录列表"""
    from app.models.export_log import ExportLog

    result = await db.execute(
        select(ExportLog)
        .where(ExportLog.tenant_id == tenant_id)
        .order_by(ExportLog.created_at.desc())
        .limit(50)
    )
    exports = result.scalars().all()
    return {
        "items": [
            {
                "id": str(e.id),
                "export_type": e.export_type,
                "resource_id": e.resource_id,
                "file_name": e.file_name,
                "row_count": e.row_count,
                "status": e.status,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in exports
        ],
        "total": len(exports),
    }


@dashboard_router.post("/exports")
async def create_export(
    export_type: str = Query(...),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    """数据导出"""
    if export_type == "scan_events":
        stmt = select(ScanEvent).where(ScanEvent.tenant_id == tenant_id)
        result = await db.execute(stmt)
        events = result.scalars().all()

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            "public_id", "scan_time", "is_first_scan", "environment",
        ])
        writer.writeheader()
        for e in events:
            writer.writerow({
                "public_id": e.public_id,
                "scan_time": e.scan_time.isoformat() if e.scan_time else "",
                "is_first_scan": e.is_first_scan,
                "environment": e.environment or "",
            })

        csv_content = output.getvalue()
        row_count = len(events)

        from app.services.export_audit import log_export
        await log_export(
            db, tenant_id, account_id, "scan_events_csv",
            file_name=f"scan-events-{tenant_id.hex[:8]}.csv",
            row_count=row_count,
        )
        await db.commit()

        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=scan-events.csv"},
        )

    raise NotImplementedError(f"Export type '{export_type}' not supported")
