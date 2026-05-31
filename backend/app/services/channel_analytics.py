"""渠道分析服务：按渠道维度聚合扫码数据、健康评分、转化率对比"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import BenefitClaim
from app.models.channel import CodeAllocation, Distributor, Region, Store
from app.models.code import CodeBatch, CodeItem
from app.models.scan import ScanEvent

# ── 维度配置 ────────────────────────────────────────

_DIMENSION_CONFIG = {
    "distributor": {
        "join_on": lambda: CodeBatch.id == CodeItem.code_batch_id,
        "group_col": lambda: CodeBatch.distributor_id,
        "not_null": lambda: CodeBatch.distributor_id.isnot(None),
        "name_model": Distributor,
        "id_field": "distributor_id",
    },
    "region": {
        "join_on": lambda: CodeBatch.id == CodeItem.code_batch_id,
        "group_col": lambda: CodeBatch.region_id,
        "not_null": lambda: CodeBatch.region_id.isnot(None),
        "name_model": Region,
        "id_field": "region_id",
    },
    "store": {
        "join_on": lambda: CodeAllocation.batch_id == CodeItem.code_batch_id,
        "group_col": lambda: CodeAllocation.store_id,
        "not_null": lambda: CodeAllocation.store_id.isnot(None),
        "name_model": Store,
        "id_field": "store_id",
    },
}


async def get_scan_by_channel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dimension: str = "distributor",
    days_back: int = 30,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """按渠道维度聚合扫码统计。"""
    if dimension not in _DIMENSION_CONFIG:
        return [], 0

    cfg = _DIMENSION_CONFIG[dimension]
    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)
    id_field = cfg["id_field"]
    group_col = cfg["group_col"]()

    subq = (
        select(
            group_col.label(id_field),
            func.count(ScanEvent.id).label("scan_count"),
            func.count(func.distinct(ScanEvent.public_id)).label("scan_uv"),
            func.count(func.distinct(ScanEvent.ip_hash)).label("distinct_ips"),
        )
        .select_from(ScanEvent)
        .join(CodeItem, CodeItem.public_id == ScanEvent.public_id)
        .join(CodeBatch if id_field != "store_id" else CodeAllocation, cfg["join_on"]())
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= cutoff,
            cfg["not_null"](),
        )
        .group_by(group_col)
    ).subquery()

    total = (await db.execute(select(func.count()).select_from(subq))).scalar() or 0

    rows = (
        await db.execute(
            select(subq)
            .order_by(subq.c.scan_count.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    # 批量查名称
    dim_ids = [getattr(r, id_field) for r in rows]
    names: dict = {}
    if dim_ids:
        name_model = cfg["name_model"]
        entities = (await db.execute(
            select(name_model).where(name_model.id.in_(dim_ids))
        )).scalars().all()
        names = {e.id: e.name for e in entities}

    items = [
        {
            id_field: str(getattr(r, id_field)),
            "name": names.get(getattr(r, id_field), "未知"),
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

    # 按渠道获取扫码数据
    items, _ = await get_scan_by_channel(db, tenant_id, dimension=dimension, days_back=days_back, page=1, page_size=100)

    # 获取跨区线索（按经销商维度）
    diversion_by_channel = {}
    if dimension == "distributor":
        div_result = await db.execute(
            select(DiversionClue.distributor_id, func.count().label("cnt"))
            .where(DiversionClue.tenant_id == tenant_id)
            .group_by(DiversionClue.distributor_id)
        )
        diversion_by_channel = {row.distributor_id: row.cnt for row in div_result.all() if row.distributor_id}

    scores = []
    for item in items:
        scan_count = item.get("scan_count", 0)
        scan_uv = item.get("scan_uv", 1) or 1

        repeat_rate = max(0, (scan_count - scan_uv) / scan_count) if scan_count > 0 else 0

        channel_key = item.get(f"{dimension}_id")
        cross_region_count = 0
        if dimension == "distributor" and channel_key:
            try:
                cross_region_count = diversion_by_channel.get(uuid.UUID(channel_key), 0)
            except (ValueError, TypeError):
                pass
        cross_region_rate = cross_region_count / scan_count if scan_count > 0 else 0

        distinct_ips = item.get("distinct_ips", 0)
        anomaly_rate = max(0, 1 - (distinct_ips / scan_uv)) if scan_uv > 0 else 0

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
    scan_items, _ = await get_scan_by_channel(
        db, tenant_id, dimension=dimension, days_back=days_back, page=1, page_size=100
    )

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
        estimated_claims = round(total_claims * scan_uv / total_uv) if total_uv > 0 else 0
        conversion = round(estimated_claims / scan_uv * 100, 2) if scan_uv > 0 else 0

        results.append({
            **item,
            "estimated_claims": estimated_claims,
            "conversion_rate": conversion,
            "vs_average": round(conversion - overall_conversion, 2),
        })

    return sorted(results, key=lambda x: x["conversion_rate"], reverse=True)
