"""统计服务层"""

import uuid
from datetime import date, timedelta

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import DailyScanStats
from app.models.code import CodeItem
from app.models.scan import ScanEvent


async def get_scan_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """获取扫码统计"""
    if not start_date:
        start_date = date.today() - timedelta(days=7)
    if not end_date:
        end_date = date.today()

    result = await db.execute(
        select(DailyScanStats)
        .where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date >= start_date,
            DailyScanStats.date <= end_date,
        )
        .order_by(DailyScanStats.date)
    )
    return [
        {
            "date": str(s.date),
            "total_scans": s.total_scans,
            "uv": s.uv,
            "first_scans": s.first_scans,
            "rescans": s.rescans,
        }
        for s in result.scalars().all()
    ]


async def get_code_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    code_batch_id: uuid.UUID | None = None,
) -> dict:
    """获取码状态统计"""
    stmt = select(CodeItem.status, func.count()).where(
        CodeItem.tenant_id == tenant_id,
    ).group_by(CodeItem.status)

    if code_batch_id:
        stmt = stmt.where(CodeItem.code_batch_id == code_batch_id)

    result = await db.execute(stmt)
    stats = {str(status): count for status, count in result.all()}
    total = sum(stats.values())

    return {
        "total": total,
        "by_status": stats,
    }


async def get_dashboard(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict:
    """获取看板数据"""
    today = date.today()
    seven_days_ago = today - timedelta(days=7)

    # 今日统计
    today_result = await db.execute(
        select(DailyScanStats).where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date == today,
        )
    )
    today_stats = today_result.scalar_one_or_none()

    # 累计统计
    total_result = await db.execute(
        select(
            func.coalesce(func.sum(DailyScanStats.total_scans), 0),
            func.coalesce(func.sum(DailyScanStats.first_scans), 0),
        ).where(DailyScanStats.tenant_id == tenant_id)
    )
    total_row = total_result.one()
    cumulative_scans = int(total_row[0])
    cumulative_first_scans = int(total_row[1])

    # 最近 7 天趋势
    trend = await get_scan_stats(db, tenant_id, seven_days_ago, today)

    # 环境占比（从 scan_events 查询）
    env_result = await db.execute(
        select(ScanEvent.environment, func.count())
        .where(ScanEvent.tenant_id == tenant_id)
        .group_by(ScanEvent.environment)
    )
    env_stats = {env or "unknown": count for env, count in env_result.all()}

    return {
        "today_scans": today_stats.total_scans if today_stats else 0,
        "today_uv": today_stats.uv if today_stats else 0,
        "cumulative_scans": cumulative_scans,
        "cumulative_first_scans": cumulative_first_scans,
        "trend": trend,
        "environment_breakdown": env_stats,
    }


async def aggregate_daily_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    target_date: date,
) -> dict:
    """聚合同一天的扫码事件到汇总表"""
    from datetime import UTC, datetime

    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
    end = start + timedelta(days=1)

    result = await db.execute(
        select(
            func.count().label("total"),
            func.count(ScanEvent.public_id.distinct()).label("uv"),
            func.sum(ScanEvent.is_first_scan.cast(Integer)).label("first"),
        ).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= start,
            ScanEvent.scan_time < end,
        )
    )
    row = result.one()

    total = row.total or 0
    first = int(row.first or 0)

    # UPSERT
    existing = await db.execute(
        select(DailyScanStats).where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date == target_date,
        )
    )
    stats = existing.scalar_one_or_none()
    if stats:
        stats.total_scans = total
        stats.uv = row.uv or 0
        stats.first_scans = first
        stats.rescans = total - first
    else:
        stats = DailyScanStats(
            tenant_id=tenant_id,
            date=target_date,
            total_scans=total,
            uv=row.uv or 0,
            first_scans=first,
            rescans=total - first,
        )
        db.add(stats)

    await db.flush()
    return {"date": str(target_date), "total": total, "first_scans": first}
