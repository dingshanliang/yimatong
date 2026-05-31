"""渠道分析服务：按渠道维度聚合扫码数据、健康评分、转化率对比"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import CodeAllocation, Distributor, Region, Store
from app.models.code import CodeBatch, CodeItem
from app.models.campaign import BenefitClaim
from app.models.scan import ScanEvent


async def get_scan_by_channel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dimension: str = "distributor",
    days_back: int = 30,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """按渠道维度聚合扫码统计。

    dimension: distributor | region | store
    """
    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)

    # 基础：ScanEvent JOIN CodeItem JOIN CodeBatch LEFT JOIN CodeAllocation
    # 简化方案：通过 CodeBatch 的 distributor_id / region_id 聚合
    if dimension == "distributor":
        return await _aggregate_by_distributor(db, tenant_id, cutoff, page, page_size)
    elif dimension == "region":
        return await _aggregate_by_region(db, tenant_id, cutoff, page, page_size)
    elif dimension == "store":
        return await _aggregate_by_store(db, tenant_id, cutoff, page, page_size)
    return [], 0


async def _aggregate_by_distributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cutoff: datetime,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """按经销商聚合扫码数据"""
    # ScanEvent → CodeItem → CodeBatch.distributor_id
    subq = (
        select(
            CodeBatch.distributor_id,
            func.count(ScanEvent.id).label("scan_count"),
            func.count(func.distinct(ScanEvent.public_id)).label("scan_uv"),
            func.count(func.distinct(ScanEvent.ip_hash)).label("distinct_ips"),
        )
        .select_from(ScanEvent)
        .join(CodeItem, CodeItem.public_id == ScanEvent.public_id)
        .join(CodeBatch, CodeBatch.id == CodeItem.code_batch_id)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= cutoff,
            CodeBatch.distributor_id.isnot(None),
        )
        .group_by(CodeBatch.distributor_id)
    ).subquery()

    total_stmt = select(func.count()).select_from(subq)
    total = (await db.execute(total_stmt)).scalar() or 0

    stmt = (
        select(subq)
        .order_by(subq.c.scan_count.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # 获取经销商名称
    dist_ids = [r.distributor_id for r in rows]
    names = {}
    if dist_ids:
        dists = (await db.execute(
            select(Distributor).where(Distributor.id.in_(dist_ids))
        )).scalars().all()
        names = {d.id: d.name for d in dists}

    items = [
        {
            "distributor_id": str(r.distributor_id),
            "name": names.get(r.distributor_id, "未知"),
            "scan_count": r.scan_count,
            "scan_uv": r.scan_uv,
            "distinct_ips": r.distinct_ips,
        }
        for r in rows
    ]
    return items, total


async def _aggregate_by_region(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cutoff: datetime,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """按区域聚合扫码数据"""
    subq = (
        select(
            CodeBatch.region_id,
            func.count(ScanEvent.id).label("scan_count"),
            func.count(func.distinct(ScanEvent.public_id)).label("scan_uv"),
            func.count(func.distinct(ScanEvent.ip_hash)).label("distinct_ips"),
        )
        .select_from(ScanEvent)
        .join(CodeItem, CodeItem.public_id == ScanEvent.public_id)
        .join(CodeBatch, CodeBatch.id == CodeItem.code_batch_id)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= cutoff,
            CodeBatch.region_id.isnot(None),
        )
        .group_by(CodeBatch.region_id)
    ).subquery()

    total_stmt = select(func.count()).select_from(subq)
    total = (await db.execute(total_stmt)).scalar() or 0

    stmt = (
        select(subq)
        .order_by(subq.c.scan_count.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rows = result.all()

    region_ids = [r.region_id for r in rows]
    names = {}
    if region_ids:
        regions = (await db.execute(
            select(Region).where(Region.id.in_(region_ids))
        )).scalars().all()
        names = {r.id: r.name for r in regions}

    items = [
        {
            "region_id": str(r.region_id),
            "name": names.get(r.region_id, "未知"),
            "scan_count": r.scan_count,
            "scan_uv": r.scan_uv,
            "distinct_ips": r.distinct_ips,
        }
        for r in rows
    ]
    return items, total


async def _aggregate_by_store(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cutoff: datetime,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """按门店聚合扫码数据（通过 CodeAllocation）"""
    subq = (
        select(
            CodeAllocation.store_id,
            func.count(ScanEvent.id).label("scan_count"),
            func.count(func.distinct(ScanEvent.public_id)).label("scan_uv"),
            func.count(func.distinct(ScanEvent.ip_hash)).label("distinct_ips"),
        )
        .select_from(ScanEvent)
        .join(CodeItem, CodeItem.public_id == ScanEvent.public_id)
        .join(CodeAllocation, CodeAllocation.batch_id == CodeItem.code_batch_id)
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= cutoff,
            CodeAllocation.store_id.isnot(None),
        )
        .group_by(CodeAllocation.store_id)
    ).subquery()

    total_stmt = select(func.count()).select_from(subq)
    total = (await db.execute(total_stmt)).scalar() or 0

    stmt = (
        select(subq)
        .order_by(subq.c.scan_count.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rows = result.all()

    store_ids = [r.store_id for r in rows]
    names = {}
    if store_ids:
        stores = (await db.execute(
            select(Store).where(Store.id.in_(store_ids))
        )).scalars().all()
        names = {s.id: s.name for s in stores}

    items = [
        {
            "store_id": str(r.store_id),
            "name": names.get(r.store_id, "未知"),
            "scan_count": r.scan_count,
            "scan_uv": r.scan_uv,
            "distinct_ips": r.distinct_ips,
        }
        for r in rows
    ]
    return items, total


async def get_channel_health_scores(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dimension: str = "distributor",
    days_back: int = 30,
) -> list[dict]:
    """渠道健康评分。

    score = 100 - (异常扫码比例×40 + 跨区率×30 + 重复扫码率×30)
    """
    from app.models.channel import DiversionClue

    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)

    # 按渠道获取扫码数据
    items, _ = await get_scan_by_channel(db, tenant_id, dimension=dimension, days_back=days_back, page=1, page_size=100)

    # 获取跨区线索（按经销商维度）
    diversion_by_channel = {}
    if dimension == "distributor":
        div_stmt = (
            select(DiversionClue.distributor_id, func.count().label("cnt"))
            .where(DiversionClue.tenant_id == tenant_id)
            .group_by(DiversionClue.distributor_id)
        )
        div_result = await db.execute(div_stmt)
        diversion_by_channel = {row.distributor_id: row.cnt for row in div_result.all() if row.distributor_id}

    # 计算每个渠道的健康评分
    scores = []
    for item in items:
        scan_count = item.get("scan_count", 0)
        scan_uv = item.get("scan_uv", 1) or 1

        # 重复扫码率：如果 scan_count > scan_uv 说明有重复
        repeat_rate = max(0, (scan_count - scan_uv) / scan_count) if scan_count > 0 else 0

        # 跨区率：从 diversion 数据获取
        channel_key = item.get(f"{dimension}_id")
        cross_region_count = 0
        if dimension == "distributor" and channel_key:
            try:
                cross_region_count = diversion_by_channel.get(uuid.UUID(channel_key), 0)
            except (ValueError, TypeError):
                pass
        cross_region_rate = cross_region_count / scan_count if scan_count > 0 else 0

        # 异常扫码比例（IP 集中度 > 50% 为异常）
        distinct_ips = item.get("distinct_ips", 0)
        anomaly_rate = max(0, 1 - (distinct_ips / scan_uv)) if scan_uv > 0 else 0

        # 健康评分
        score = max(0, 100 - (anomaly_rate * 40 + cross_region_rate * 30 + repeat_rate * 30))
        score = round(score, 1)

        level = "healthy"
        if score < 60:
            level = "danger"
        elif score < 80:
            level = "warning"

        scores.append({
            **item,
            "health_score": score,
            "level": level,
            "repeat_rate": round(repeat_rate * 100, 1),
            "cross_region_rate": round(cross_region_rate * 100, 1),
            "anomaly_rate": round(anomaly_rate * 100, 1),
        })

    return sorted(scores, key=lambda x: x["health_score"])


async def get_conversion_comparison(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dimension: str = "distributor",
    days_back: int = 30,
) -> list[dict]:
    """渠道间转化率对比：扫码 UV → 权益领取数"""
    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)

    # 扫码数据
    scan_items, _ = await get_scan_by_channel(db, tenant_id, dimension=dimension, days_back=days_back, page=1, page_size=100)

    # 权益领取数据（通过 campaign 关联渠道较复杂，简化：按租户统计总领取数除以渠道数）
    claim_result = await db.execute(
        select(func.count())
        .select_from(BenefitClaim)
        .where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.status == "success",
        )
    )
    total_claims = claim_result.scalar() or 0

    total_uv = sum(item.get("scan_uv", 0) for item in scan_items)
    overall_conversion = round(total_claims / total_uv * 100, 2) if total_uv > 0 else 0

    results = []
    for item in scan_items:
        scan_uv = item.get("scan_uv", 0)
        # 简化：假设领取均匀分布到各渠道（精确计算需要 claim → consumer → scan → batch 链路）
        estimated_claims = round(total_claims * scan_uv / total_uv) if total_uv > 0 else 0
        conversion = round(estimated_claims / scan_uv * 100, 2) if scan_uv > 0 else 0

        results.append({
            **item,
            "estimated_claims": estimated_claims,
            "conversion_rate": conversion,
            "vs_average": round(conversion - overall_conversion, 2),
        })

    return sorted(results, key=lambda x: x["conversion_rate"], reverse=True)
