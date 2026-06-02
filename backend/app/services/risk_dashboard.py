"""渠道风控看板服务"""

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Distributor, DiversionClue
from app.models.risk import RiskAlert
from app.models.scan import ScanEvent


async def get_repeat_scan_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    min_count: int = 2,
    page: int = 1,
    page_size: int = 20,
    days_back: int = 30,
) -> tuple[list[dict], int]:
    """按码统计重复扫码次数"""
    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)
    subq = (
        select(
            ScanEvent.public_id,
            func.count().label("scan_count"),
            func.count(func.distinct(ScanEvent.ip_hash)).label("distinct_ips"),
        )
        .where(ScanEvent.tenant_id == tenant_id, ScanEvent.scan_time >= cutoff)
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
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
) -> dict:
    """跨区扫码统计（支持时间段筛选）"""
    conditions = [DiversionClue.tenant_id == tenant_id]

    total_stmt = select(func.count()).select_from(DiversionClue).where(*conditions)
    total_result = await db.execute(total_stmt)
    total_clues = total_result.scalar() or 0

    # 未处理数
    unresolved_stmt = (
        select(func.count())
        .select_from(DiversionClue)
        .where(DiversionClue.tenant_id == tenant_id, DiversionClue.resolved.is_(False))
    )
    unresolved_result = await db.execute(unresolved_stmt)
    unresolved_count = unresolved_result.scalar() or 0

    # 按预期区域分布
    by_region_stmt = (
        select(DiversionClue.expected_region, func.count().label("cnt"))
        .where(*conditions)
        .group_by(DiversionClue.expected_region)
        .order_by(func.count().desc())
    )
    region_result = await db.execute(by_region_stmt)
    by_region = [{"region": row.expected_region, "count": row.cnt} for row in region_result.all()]

    # 按实际扫码城市分布
    by_city_stmt = (
        select(DiversionClue.detected_city, func.count().label("cnt"))
        .where(*conditions)
        .group_by(DiversionClue.detected_city)
        .order_by(func.count().desc())
    )
    city_result = await db.execute(by_city_stmt)
    by_detected_city = [{"city": row.detected_city, "count": row.cnt} for row in city_result.all()]

    # 按码统计跨区次数 top 10
    by_code_stmt = (
        select(DiversionClue.public_id, func.count().label("cnt"))
        .where(*conditions)
        .group_by(DiversionClue.public_id)
        .order_by(func.count().desc())
        .limit(10)
    )
    code_result = await db.execute(by_code_stmt)
    by_code = [{"public_id": row.public_id, "count": row.cnt} for row in code_result.all()]

    return {
        "total_clues": total_clues,
        "unresolved_count": unresolved_count,
        "by_region": by_region,
        "by_detected_city": by_detected_city,
        "by_code": by_code,
    }


async def get_cross_region_trend(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
) -> list[dict]:
    """跨区扫码趋势（按天统计）"""
    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)

    # UUID v7 前 48 位是毫秒时间戳，用 PostgreSQL 函数提取日期
    from sqlalchemy import Date as SqlDate
    from sqlalchemy import String, cast, text

    uuid_ts_expr = func.to_timestamp(
        ("x" + func.substr(cast(DiversionClue.id, String), 1, 12)).cast(text("bigint")) / 1000
    )
    stmt = (
        select(
            cast(uuid_ts_expr, SqlDate).label("stat_date"),
            func.count().label("cnt"),
        )
        .where(
            DiversionClue.tenant_id == tenant_id,
            uuid_ts_expr >= cutoff,
        )
        .group_by("stat_date")
        .order_by("stat_date")
    )
    result = await db.execute(stmt)
    return [{"date": str(row.stat_date), "count": row.cnt} for row in result.all()]


async def get_diversion_summary(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    resolved: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """窜货线索汇总"""
    stmt = select(DiversionClue).where(DiversionClue.tenant_id == tenant_id)
    count_stmt = (
        select(func.count())
        .select_from(DiversionClue)
        .where(
            DiversionClue.tenant_id == tenant_id,
        )
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
        dists_result = await db.execute(select(Distributor).where(Distributor.id.in_(dist_ids)))
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


async def resolve_diversion_clue(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    clue_id: uuid.UUID,
    resolution_action: str | None = None,
    resolution_note: str | None = None,
    resolved_by_account_id: uuid.UUID | None = None,
) -> DiversionClue | None:
    """标记窜货线索为已处理"""
    result = await db.execute(
        select(DiversionClue).where(
            DiversionClue.id == clue_id,
            DiversionClue.tenant_id == tenant_id,
        )
    )
    clue = result.scalar_one_or_none()
    if not clue:
        return None
    clue.resolved = True
    clue.resolution_action = resolution_action
    clue.resolution_note = resolution_note
    clue.resolved_by_account_id = resolved_by_account_id
    clue.resolved_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(clue)
    return clue


async def export_risk_data(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data_type: str,
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
