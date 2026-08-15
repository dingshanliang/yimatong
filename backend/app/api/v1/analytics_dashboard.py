"""数据看板 API（活动看板、风控看板、区域看板、导出）"""

import hashlib
import io
import re
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.models.campaign import BenefitClaim, Campaign
from app.models.scan import ScanEvent
from app.schemas.export import AnalyticsExportRequest
from app.services.code_export import spreadsheet_safe
from app.services.export_access import (
    CanonicalExportIdempotencyKey,
    record_authorized_prepared_export,
    require_export_auth_session,
)
from app.services.export_admission import enforce_export_rate_limit
from app.utils.auth_rbac import require_permission

# 导出行数上限，防止大数据量导致内存溢出
_EXPORT_ROW_LIMIT = 50_000

dashboard_router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["analytics-dashboards"],
    dependencies=[Depends(require_permission("analytics:view"))],
)


def require_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    tenant_type = getattr(request.state, "tenant_type", None)
    if role != "admin" or tenant_type != "brand":
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
    campaign_stmt = campaign_stmt.order_by(Campaign.id).limit(_EXPORT_ROW_LIMIT)
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
    cutoff = datetime.combine(start_date or (date.today() - timedelta(days=30)), datetime.min.time(), tzinfo=UTC)
    claim_stmt = claim_stmt.where(BenefitClaim.created_at >= cutoff, BenefitClaim.status == "success")
    if end_date:
        end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)
        claim_stmt = claim_stmt.where(BenefitClaim.created_at < end_dt)
    claim_result = await db.execute(claim_stmt)
    claim_map: dict[uuid.UUID, int] = dict(claim_result.all())

    items = [
        {
            "campaign_id": str(c.id),
            "campaign_name": c.name,
            "status": c.status,
            "claim_count": claim_map.get(c.id, 0),
            "scan_count": None,
            "conversion_rate": None,
            "conversion_status": "unavailable",
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
    return await _get_risk_dashboard(db, tenant_id)


async def _get_risk_dashboard(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Return the exact analytics risk-dashboard read model."""
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
    _: None = Depends(require_admin),
):
    """导出记录列表"""
    from app.models.export_log import ExportLog

    result = await db.execute(
        select(
            ExportLog.id,
            ExportLog.export_type,
            ExportLog.resource_id,
            ExportLog.file_name,
            ExportLog.row_count,
            ExportLog.status,
            ExportLog.created_at,
        )
        .where(ExportLog.tenant_id == tenant_id)
        .order_by(ExportLog.created_at.desc())
        .limit(50)
    )
    exports = result.all()
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


def _build_xlsx_sheets(sheets: list[tuple[str, list[str], list[list]]]) -> bytes:
    """Build a deterministic workbook from one or more read-model tables."""
    wb = Workbook()
    workbook_epoch = datetime(2000, 1, 1, tzinfo=UTC)
    wb.properties.created = workbook_epoch
    wb.properties.modified = workbook_epoch
    ws = wb.active
    for index, (sheet_name, headers, rows) in enumerate(sheets):
        if index:
            ws = wb.create_sheet()
        ws.title = sheet_name
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append([spreadsheet_safe(value) if isinstance(value, str) else value for value in row])
    buf = io.BytesIO()
    wb.save(buf)
    # XLSX is a ZIP container. Canonical entry timestamps/order make the file
    # byte-stable so an idempotent retry cannot conflict with its own checksum.
    canonical = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue()), "r") as source:
        with zipfile.ZipFile(canonical, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for name in sorted(source.namelist()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                entry = source.read(name)
                if name == "docProps/core.xml":
                    entry = re.sub(
                        rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                        rb"\g<1>2000-01-01T00:00:00Z\g<2>",
                        entry,
                    )
                target.writestr(info, entry, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return canonical.getvalue()


def _build_xlsx(headers: list[str], rows: list[list], sheet_name: str = "Sheet1") -> bytes:
    """Build one deterministic XLSX table."""

    return _build_xlsx_sheets([(sheet_name, headers, rows)])


@dashboard_router.post("/exports")
async def create_export(
    body: AnalyticsExportRequest,
    idempotency_key: CanonicalExportIdempotencyKey,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_admin),
):
    """数据导出（仅管理员），支持 xlsx 格式"""
    auth_session_id = require_export_auth_session(request)
    await enforce_export_rate_limit(tenant_id, uuid.UUID(str(request.state.account_id)))

    async def prepared_response(
        *,
        content: bytes,
        canonical_type: str,
        file_name: str,
        row_count: int,
        scope_snapshot: dict,
        resource_id: uuid.UUID | None = None,
    ) -> StreamingResponse:
        checksum = hashlib.sha256(content).hexdigest()
        prepared = await record_authorized_prepared_export(
            db,
            tenant_id=tenant_id,
            auth_session_id=auth_session_id,
            export_id=uuid7(),
            export_type=canonical_type,
            reason=body.reason,
            scope_snapshot=scope_snapshot,
            idempotency_key=idempotency_key,
            file_name=file_name,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            row_count=row_count,
            checksum_sha256=checksum,
            file_size_bytes=len(content),
            resource_id=resource_id,
        )
        await db.commit()
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={file_name}",
                "Content-Length": str(prepared.file_size_bytes),
                "X-Content-SHA256": prepared.checksum_sha256,
                "X-Export-Id": str(prepared.export_id),
            },
        )

    if body.export_type == "scan_events":
        stmt = select(ScanEvent).where(ScanEvent.tenant_id == tenant_id)
        if body.start_date:
            cutoff = datetime(body.start_date.year, body.start_date.month, body.start_date.day, tzinfo=UTC)
            stmt = stmt.where(ScanEvent.scan_time >= cutoff)
        if body.end_date:
            end_dt = datetime(
                body.end_date.year,
                body.end_date.month,
                body.end_date.day,
                tzinfo=UTC,
            ) + timedelta(days=1)
            stmt = stmt.where(ScanEvent.scan_time < end_dt)
        stmt = stmt.order_by(ScanEvent.scan_time, ScanEvent.id).limit(_EXPORT_ROW_LIMIT)
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
        download_name = "scan-events.xlsx"
        return await prepared_response(
            content=xlsx_bytes,
            canonical_type="scan_events_xlsx",
            file_name=download_name,
            row_count=len(events),
            scope_snapshot={
                "start_date": body.start_date.isoformat() if body.start_date else None,
                "end_date": body.end_date.isoformat() if body.end_date else None,
                "time_field": "scan_time",
                "row_limit": _EXPORT_ROW_LIMIT,
            },
        )

    if body.export_type == "scan_stats":
        from app.models.analytics import DailyScanStats

        stmt = select(DailyScanStats).where(DailyScanStats.tenant_id == tenant_id)
        if body.start_date:
            stmt = stmt.where(DailyScanStats.date >= body.start_date)
        if body.end_date:
            stmt = stmt.where(DailyScanStats.date <= body.end_date)
        stmt = stmt.order_by(DailyScanStats.date).limit(_EXPORT_ROW_LIMIT)
        result = await db.execute(stmt)
        stats = result.scalars().all()

        headers = ["日期", "扫码量", "独立用户", "首扫数", "重扫数"]
        rows = [[str(s.date), s.total_scans, s.uv, s.first_scans, s.rescans] for s in stats]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="扫码统计")
        download_name = "scan-stats.xlsx"
        return await prepared_response(
            content=xlsx_bytes,
            canonical_type="scan_stats_xlsx",
            file_name=download_name,
            row_count=len(stats),
            scope_snapshot={
                "start_date": body.start_date.isoformat() if body.start_date else None,
                "end_date": body.end_date.isoformat() if body.end_date else None,
                "row_limit": _EXPORT_ROW_LIMIT,
            },
        )

    if body.export_type == "campaign_dashboard":
        items, _ = await _get_campaign_stats(
            db,
            tenant_id,
            campaign_id=body.campaign_id,
            start_date=body.start_date,
            end_date=body.end_date,
        )

        headers = ["活动 ID", "活动名称", "状态", "领取数", "扫码数"]
        rows = [
            [item["campaign_id"], item["campaign_name"], item["status"], item["claim_count"], item["scan_count"]]
            for item in items
        ]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="活动看板")
        download_name = "campaign-dashboard.xlsx"
        return await prepared_response(
            content=xlsx_bytes,
            canonical_type="campaign_dashboard_xlsx",
            file_name=download_name,
            row_count=len(items),
            resource_id=body.campaign_id,
            scope_snapshot={
                "campaign_id": str(body.campaign_id) if body.campaign_id else None,
                "start_date": body.start_date.isoformat() if body.start_date else None,
                "end_date": body.end_date.isoformat() if body.end_date else None,
                "claim_status": "success",
            },
        )

    if body.export_type == "risk_dashboard":
        # The compatibility export remains on this endpoint, but its stronger
        # risk permission is checked explicitly before its first data query.
        await require_permission("risk:read")(request)
        dashboard = await _get_risk_dashboard(db, tenant_id)
        rows = [[key, value] for key, value in sorted(dashboard["type_stats"].items())]
        rows.extend(
            [
                ["unresolved_count", dashboard["unresolved_count"]],
                ["total_alerts", dashboard["total_alerts"]],
            ]
        )
        content = _build_xlsx(["指标", "数量"], rows, sheet_name="风控看板")
        return await prepared_response(
            content=content,
            canonical_type="risk_dashboard_xlsx",
            file_name="risk-dashboard.xlsx",
            row_count=len(rows),
            scope_snapshot={
                "read_model": "/api/v1/analytics/risk-dashboard",
                "dimensions": ["type_stats", "unresolved_count", "total_alerts"],
            },
        )

    if body.export_type == "regional_dashboard":
        from app.services.regional import get_regional_dashboard, verify_org_access

        assert body.org_id is not None and body.days_back is not None
        await verify_org_access(db, body.org_id, tenant_id)
        dashboard = await get_regional_dashboard(db, body.org_id, days_back=body.days_back)
        overview_rows = [
            ["member_count", dashboard["member_count"]],
            ["product_count", dashboard["product_count"]],
            ["total_scans", dashboard["total_scans"]],
            ["total_claims", dashboard["total_claims"]],
            ["days_back", dashboard["days_back"]],
        ]
        member_rows = [
            [item["tenant_id"], item["member_name"], item["scan_count"], item["claim_count"]]
            for item in dashboard["by_member"]
        ]
        product_rows = [
            [item["product_id"], item["product_name"], item["scan_count"]] for item in dashboard["by_product"]
        ]
        if len(overview_rows) + len(member_rows) + len(product_rows) > _EXPORT_ROW_LIMIT:
            raise HTTPException(status_code=413, detail="Regional export exceeds the row limit")
        xlsx_bytes = _build_xlsx_sheets(
            [
                ("区域概览", ["指标", "值"], overview_rows),
                ("成员企业", ["租户 ID", "企业名称", "扫码量", "领取量"], member_rows),
                ("产品", ["产品 ID", "产品名称", "扫码量"], product_rows),
            ]
        )
        download_name = "regional-dashboard.xlsx"
        return await prepared_response(
            content=xlsx_bytes,
            canonical_type="regional_dashboard_xlsx",
            file_name=download_name,
            row_count=len(overview_rows) + len(member_rows) + len(product_rows),
            resource_id=body.org_id,
            scope_snapshot={
                "org_id": str(body.org_id),
                "days_back": body.days_back,
                "dimensions": [
                    "member_count",
                    "product_count",
                    "total_scans",
                    "total_claims",
                    "days_back",
                    "by_member",
                    "by_product",
                ],
            },
        )

    raise HTTPException(status_code=400, detail=f"Export type '{body.export_type}' not supported")
