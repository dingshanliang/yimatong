"""数据看板 API（活动看板、风控看板、区域看板、导出）"""

import io
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.campaign import BenefitClaim, Campaign
from app.models.scan import ScanEvent

# 导出行数上限，防止大数据量导致内存溢出
_EXPORT_ROW_LIMIT = 50_000

dashboard_router = APIRouter(prefix="/api/v1/analytics", tags=["analytics-dashboards"])


def require_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")


async def _get_campaign_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[dict], int]:
    """批量获取活动看板数据（3 次查询，无 N+1）"""
    campaign_stmt = select(Campaign).where(Campaign.tenant_id == tenant_id)
    if campaign_id:
        campaign_stmt = campaign_stmt.where(Campaign.id == campaign_id)
    result = await db.execute(campaign_stmt)
    campaigns = result.scalars().all()

    if not campaigns:
        return [], 0

    campaign_ids = [c.id for c in campaigns]

    # 批量领取数（1 次 GROUP BY 查询）
    claim_stmt = (
        select(BenefitClaim.campaign_id, func.count())
        .where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.campaign_id.in_(campaign_ids),
        )
        .group_by(BenefitClaim.campaign_id)
    )
    claim_result = await db.execute(claim_stmt)
    claim_map: dict[uuid.UUID, int] = dict(claim_result.all())

    # 总扫码数（1 次查询，日期范围过滤）
    cutoff = datetime.combine(start_date or (date.today() - timedelta(days=30)), datetime.min.time(), tzinfo=UTC)
    scan_stmt = (
        select(func.count())
        .select_from(ScanEvent)
        .where(ScanEvent.tenant_id == tenant_id, ScanEvent.scan_time >= cutoff)
    )
    if end_date:
        end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)
        scan_stmt = scan_stmt.where(ScanEvent.scan_time < end_dt)
    scan_result = await db.execute(scan_stmt)
    scan_count = scan_result.scalar() or 0

    items = [
        {
            "campaign_id": str(c.id),
            "campaign_name": c.name,
            "status": c.status,
            "claim_count": claim_map.get(c.id, 0),
            "scan_count": scan_count,
        }
        for c in campaigns
    ]
    return items, len(items)


@dashboard_router.get("/campaign-dashboard")
async def campaign_dashboard(
    campaign_id: uuid.UUID | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """活动看板数据"""
    items, total = await _get_campaign_stats(db, tenant_id, campaign_id, start_date, end_date)
    return {"items": items, "total": total}


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
    unresolved_stmt = (
        select(func.count())
        .select_from(RiskAlert)
        .where(
            RiskAlert.tenant_id == tenant_id,
            not RiskAlert.resolved,
        )
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
    clue_stats_stmt = (
        select(DiversionClue.resolved, func.count())
        .where(DiversionClue.tenant_id == tenant_id)
        .group_by(DiversionClue.resolved)
    )
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
        select(ExportLog).where(ExportLog.tenant_id == tenant_id).order_by(ExportLog.created_at.desc()).limit(50)
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


def _build_xlsx(headers: list[str], rows: list[list], sheet_name: str = "Sheet1") -> bytes:
    """用 openpyxl 生成 xlsx 字节流，表头加粗。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@dashboard_router.post("/exports")
async def create_export(
    export_type: str = Query(...),
    format: str = Query("xlsx", description="导出格式: xlsx"),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_admin),
):
    """数据导出（仅管理员），支持 xlsx 格式"""
    from app.services.export_audit import log_export

    if export_type == "scan_events":
        stmt = select(ScanEvent).where(ScanEvent.tenant_id == tenant_id)
        if start_date:
            cutoff = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
            stmt = stmt.where(ScanEvent.scan_time >= cutoff)
        if end_date:
            end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)
            stmt = stmt.where(ScanEvent.scan_time < end_dt)
        stmt = stmt.limit(_EXPORT_ROW_LIMIT)
        result = await db.execute(stmt)
        events = result.scalars().all()

        headers = ["码 ID", "扫码时间", "是否首扫", "环境"]
        rows = [
            [
                e.public_id,
                e.scan_time.isoformat() if e.scan_time else "",
                "是" if e.is_first_scan else "否",
                e.environment or "",
            ]
            for e in events
        ]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="扫码事件")
        file_name = f"scan-events-{tenant_id.hex[:8]}.xlsx"
        download_name = "scan-events.xlsx"

        await log_export(
            db,
            tenant_id,
            account_id,
            "scan_events_xlsx",
            file_name=file_name,
            row_count=len(events),
        )
        await db.commit()

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={download_name}"},
        )

    if export_type == "scan_stats":
        from app.models.analytics import DailyScanStats

        stmt = select(DailyScanStats).where(DailyScanStats.tenant_id == tenant_id)
        if start_date:
            stmt = stmt.where(DailyScanStats.date >= start_date)
        if end_date:
            stmt = stmt.where(DailyScanStats.date <= end_date)
        stmt = stmt.order_by(DailyScanStats.date).limit(_EXPORT_ROW_LIMIT)
        result = await db.execute(stmt)
        stats = result.scalars().all()

        headers = ["日期", "扫码量", "独立用户", "首扫数", "重扫数"]
        rows = [[str(s.date), s.total_scans, s.uv, s.first_scans, s.rescans] for s in stats]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="扫码统计")
        file_name = f"scan-stats-{tenant_id.hex[:8]}.xlsx"
        download_name = "scan-stats.xlsx"

        await log_export(
            db,
            tenant_id,
            account_id,
            "scan_stats_xlsx",
            file_name=file_name,
            row_count=len(stats),
        )
        await db.commit()

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={download_name}"},
        )

    if export_type == "campaign_dashboard":
        items, _ = await _get_campaign_stats(db, tenant_id, start_date=start_date, end_date=end_date)

        headers = ["活动 ID", "活动名称", "状态", "领取数", "扫码数"]
        rows = [
            [item["campaign_id"], item["campaign_name"], item["status"], item["claim_count"], item["scan_count"]]
            for item in items
        ]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="活动看板")
        file_name = f"campaign-dashboard-{tenant_id.hex[:8]}.xlsx"
        download_name = "campaign-dashboard.xlsx"

        await log_export(
            db,
            tenant_id,
            account_id,
            "campaign_dashboard_xlsx",
            file_name=file_name,
            row_count=len(items),
        )
        await db.commit()

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={download_name}"},
        )

    if export_type == "risk_dashboard":
        from app.models.risk import RiskAlert

        stmt = select(RiskAlert).where(RiskAlert.tenant_id == tenant_id).limit(_EXPORT_ROW_LIMIT)
        result = await db.execute(stmt)
        alerts = result.scalars().all()

        headers = ["预警 ID", "预警类型", "码 ID", "详情", "已处理"]
        rows = [[str(a.id), a.alert_type, a.public_id, a.detail or "", "是" if a.resolved else "否"] for a in alerts]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="风控看板")
        file_name = f"risk-dashboard-{tenant_id.hex[:8]}.xlsx"
        download_name = "risk-dashboard.xlsx"

        await log_export(
            db,
            tenant_id,
            account_id,
            "risk_dashboard_xlsx",
            file_name=file_name,
            row_count=len(alerts),
        )
        await db.commit()

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={download_name}"},
        )

    if export_type == "regional_dashboard":
        from app.models.channel import DiversionClue

        stmt = select(DiversionClue).where(DiversionClue.tenant_id == tenant_id).limit(_EXPORT_ROW_LIMIT)
        result = await db.execute(stmt)
        clues = result.scalars().all()

        headers = ["线索 ID", "码 ID", "预期区域", "实际城市", "已处理"]
        rows = [
            [str(c.id), c.public_id, c.expected_region or "", c.detected_city or "", "是" if c.resolved else "否"]
            for c in clues
        ]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="区域看板")
        file_name = f"regional-dashboard-{tenant_id.hex[:8]}.xlsx"
        download_name = "regional-dashboard.xlsx"

        await log_export(
            db,
            tenant_id,
            account_id,
            "regional_dashboard_xlsx",
            file_name=file_name,
            row_count=len(clues),
        )
        await db.commit()

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={download_name}"},
        )

    raise HTTPException(status_code=400, detail=f"Export type '{export_type}' not supported")
