"""渠道风控看板服务"""

import csv
import io
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Distributor, DiversionClue
from app.models.risk import RiskAlert
from app.models.scan import ScanEvent


async def get_repeat_scan_stats(
    db: AsyncSession, tenant_id: uuid.UUID, min_count: int = 2,
    page: int = 1, page_size: int = 20,
) -> tuple[list[dict], int]:
    """按码统计重复扫码次数"""
    subq = (
        select(
            ScanEvent.public_id,
            func.count().label("scan_count"),
            func.count(func.distinct(ScanEvent.ip_hash)).label("distinct_ips"),
        )
        .where(ScanEvent.tenant_id == tenant_id)
        .group_by(ScanEvent.public_id)
        .having(func.count() >= min_count)
        .subquery()
    )

    count_stmt = select(func.count()).select_from(subq)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = (
        select(subq.c.public_id, subq.c.scan_count, subq.c.distinct_ips)
        .order_by(subq.c.scan_count.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    items = [
        {"public_id": row.public_id, "scan_count": row.scan_count, "distinct_ips": row.distinct_ips}
        for row in result.all()
    ]
    return items, total


async def get_cross_region_stats(
    db: AsyncSession, tenant_id: uuid.UUID,
) -> dict:
    """跨区扫码统计"""
    total_stmt = select(func.count()).select_from(DiversionClue).where(
        DiversionClue.tenant_id == tenant_id,
    )
    total_result = await db.execute(total_stmt)
    total_clues = total_result.scalar() or 0

    by_region_stmt = (
        select(DiversionClue.expected_region, func.count().label("cnt"))
        .where(DiversionClue.tenant_id == tenant_id)
        .group_by(DiversionClue.expected_region)
    )
    region_result = await db.execute(by_region_stmt)
    by_region = [
        {"region": row.expected_region, "count": row.cnt}
        for row in region_result.all()
    ]

    return {"total_clues": total_clues, "by_region": by_region}


async def get_diversion_summary(
    db: AsyncSession, tenant_id: uuid.UUID, resolved: bool | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    """窜货线索汇总"""
    stmt = select(DiversionClue).where(DiversionClue.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(DiversionClue).where(
        DiversionClue.tenant_id == tenant_id,
    )

    if resolved is not None:
        stmt = stmt.where(DiversionClue.resolved == resolved)
        count_stmt = count_stmt.where(DiversionClue.resolved == resolved)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    clues_result = await db.execute(
        stmt.order_by(DiversionClue.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    clues = list(clues_result.scalars().all())

    by_dist_stmt = (
        select(DiversionClue.distributor_id, func.count().label("cnt"))
        .where(DiversionClue.tenant_id == tenant_id)
        .group_by(DiversionClue.distributor_id)
    )
    if resolved is not None:
        by_dist_stmt = by_dist_stmt.where(DiversionClue.resolved == resolved)
    dist_result = await db.execute(by_dist_stmt)
    dist_rows = dist_result.all()

    dist_ids = [row.distributor_id for row in dist_rows if row.distributor_id]
    by_distributor = []
    if dist_ids:
        dists_result = await db.execute(
            select(Distributor).where(Distributor.id.in_(dist_ids))
        )
        dists = {d.id: d.name for d in dists_result.scalars().all()}
        by_distributor = [
            {"distributor_id": str(did), "name": dists.get(did, "未知"), "count": cnt}
            for row in dist_rows
            if row.distributor_id
            for did, cnt in [(row.distributor_id, row.cnt)]
        ]

    items = [
        {
            "id": str(c.id),
            "public_id": c.public_id,
            "expected_region": c.expected_region,
            "detected_city": c.detected_city,
            "resolved": c.resolved,
        }
        for c in clues
    ]

    return {
        "total": total,
        "items": items,
        "by_distributor": by_distributor,
    }


async def export_risk_data(
    db: AsyncSession, tenant_id: uuid.UUID, data_type: str,
) -> str:
    """导出风控数据为 CSV"""
    output = io.StringIO()
    writer = csv.writer(output)

    if data_type == "alerts":
        writer.writerow(["id", "alert_type", "public_id", "detail", "resolved"])
        result = await db.execute(
            select(RiskAlert).where(RiskAlert.tenant_id == tenant_id).order_by(RiskAlert.id.desc())
        )
        for alert in result.scalars().all():
            writer.writerow([str(alert.id), alert.alert_type, alert.public_id, alert.detail, alert.resolved])

    elif data_type == "diversions":
        writer.writerow(["id", "public_id", "expected_region", "detected_city", "resolved"])
        result = await db.execute(
            select(DiversionClue).where(DiversionClue.tenant_id == tenant_id).order_by(DiversionClue.id.desc())
        )
        for clue in result.scalars().all():
            writer.writerow([str(clue.id), clue.public_id, clue.expected_region, clue.detected_city, clue.resolved])

    return output.getvalue()
