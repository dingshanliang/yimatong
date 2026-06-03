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
    stmt = (
        select(CodeItem.status, func.count())
        .where(
            CodeItem.tenant_id == tenant_id,
        )
        .group_by(CodeItem.status)
    )

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
    days_back: int = 30,
) -> dict:
    """获取看板数据"""
    today = date.today()
    seven_days_ago = today - timedelta(days=7)
    cutoff = today - timedelta(days=days_back)

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

    # 环境占比（从 scan_events 查询，限制日期范围避免全表扫描）
    from datetime import UTC, datetime

    cutoff_dt = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=UTC)
    env_result = await db.execute(
        select(ScanEvent.environment, func.count())
        .where(ScanEvent.tenant_id == tenant_id, ScanEvent.scan_time >= cutoff_dt)
        .group_by(ScanEvent.environment)
    )
    env_stats = {env or "unknown": count for env, count in env_result.all()}

    # 同期对比
    period_days = (today - seven_days_ago).days + 1
    prev_start = seven_days_ago - timedelta(days=period_days)
    prev_end = seven_days_ago - timedelta(days=1)

    prev_result = await db.execute(
        select(
            func.coalesce(func.sum(DailyScanStats.total_scans), 0),
            func.coalesce(func.sum(DailyScanStats.first_scans), 0),
        ).where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date >= prev_start,
            DailyScanStats.date <= prev_end,
        )
    )
    prev_row = prev_result.one()
    prev_scans = int(prev_row[0])
    prev_first_scans = int(prev_row[1])

    def _calc_change(current: int, previous: int) -> dict | None:
        if previous == 0:
            return None
        pct = round((current - previous) / previous * 100, 1)
        return {"value": pct, "direction": "up" if pct > 0 else "down" if pct < 0 else "flat"}

    return {
        "today_scans": today_stats.total_scans if today_stats else 0,
        "today_uv": today_stats.uv if today_stats else 0,
        "cumulative_scans": cumulative_scans,
        "cumulative_first_scans": cumulative_first_scans,
        "trend": trend,
        "environment_breakdown": env_stats,
        "comparison": {
            "weekly_scans_change": _calc_change(
                sum(s["total_scans"] for s in trend), prev_scans
            ),
            "weekly_first_scans_change": _calc_change(
                sum(s["first_scans"] for s in trend), prev_first_scans
            ),
        },
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


async def get_campaign_scan_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """获取活动维度的扫码统计

    通过 Campaign.product_id → CodeBatch.product_id → CodeItem → ScanEvent 关联
    """
    from datetime import UTC, datetime

    from app.models.code import CodeBatch

    if not start_date:
        start_date = date.today() - timedelta(days=30)
    if not end_date:
        end_date = date.today()

    # 找到匹配的码批次
    batch_stmt = select(CodeBatch.id).where(CodeBatch.tenant_id == tenant_id)
    if campaign_id:
        # 通过 product_id 关联
        from app.models.campaign import Campaign

        campaign_result = await db.execute(
            select(Campaign.product_id).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_id,
            )
        )
        product_id = campaign_result.scalar_one_or_none()
        if not product_id:
            return []
        batch_stmt = batch_stmt.where(CodeBatch.product_id == product_id)

    batch_result = await db.execute(batch_stmt)
    batch_ids = [row[0] for row in batch_result.all()]

    if not batch_ids:
        return []

    # 查找这些批次关联的码的 public_id
    code_stmt = select(CodeItem.public_id).where(
        CodeItem.tenant_id == tenant_id,
        CodeItem.code_batch_id.in_(batch_ids),
    )
    code_result = await db.execute(code_stmt)
    public_ids = [row[0] for row in code_result.all()]

    if not public_ids:
        return []

    # 按日期分组统计 scan_events
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)

    result = await db.execute(
        select(
            func.date_trunc("day", ScanEvent.scan_time).label("day"),
            func.count().label("total_scans"),
            func.count(ScanEvent.public_id.distinct()).label("uv"),
            func.sum(ScanEvent.is_first_scan.cast(Integer)).label("first_scans"),
        )
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id.in_(public_ids),
            ScanEvent.scan_time >= start_dt,
            ScanEvent.scan_time < end_dt,
        )
        .group_by("day")
        .order_by("day")
    )

    rows = result.all()
    return [
        {
            "date": str(row.day.date()) if row.day else "",
            "total_scans": row.total_scans or 0,
            "uv": row.uv or 0,
            "first_scans": int(row.first_scans or 0),
            "rescans": (row.total_scans or 0) - int(row.first_scans or 0),
        }
        for row in rows
    ]
