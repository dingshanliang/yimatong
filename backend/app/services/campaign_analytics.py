"""活动看板服务"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, BenefitClaim, Campaign


async def get_campaign_funnel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> dict:
    """获取单个活动的漏斗数据"""
    # 验证活动存在
    campaign = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))
    if not campaign.scalar_one_or_none():
        return {}

    # 统计各步骤数量
    claims_count = await db.execute(
        select(func.count())
        .select_from(BenefitClaim)
        .where(
            BenefitClaim.campaign_id == campaign_id,
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.claim_type == "claim",
        )
    )
    claims = claims_count.scalar() or 0

    benefits_count = await db.execute(
        select(func.count())
        .select_from(Benefit)
        .where(
            Benefit.campaign_id == campaign_id,
            Benefit.tenant_id == tenant_id,
        )
    )
    total_benefits = benefits_count.scalar() or 0

    return {
        "campaign_id": str(campaign_id),
        "steps": [
            {"name": "扫码", "count": 0, "note": "从scan_events统计"},
            {"name": "页面浏览", "count": 0, "note": "从scan_events统计"},
            {"name": "领取权益", "count": claims},
            {"name": "权益数", "count": total_benefits},
        ],
    }


async def get_campaign_comparison(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_ids: list[uuid.UUID],
) -> list[dict]:
    """获取多活动对比数据（单次查询，避免 N+1）"""
    if not campaign_ids:
        return []

    # 一次性获取所有活动的名称和领取数
    rows = await db.execute(
        select(
            Campaign.id,
            Campaign.name,
            func.count(BenefitClaim.id).label("claims"),
        )
        .outerjoin(BenefitClaim, (BenefitClaim.campaign_id == Campaign.id) & (BenefitClaim.tenant_id == tenant_id))
        .where(Campaign.id.in_(campaign_ids), Campaign.tenant_id == tenant_id)
        .group_by(Campaign.id, Campaign.name)
    )

    # 保持原始 campaign_ids 顺序
    result_map = {
        row.id: {"campaign_id": str(row.id), "campaign_name": row.name, "claims": row.claims} for row in rows.all()
    }
    return [
        result_map.get(cid, {"campaign_id": str(cid), "campaign_name": "Unknown", "claims": 0}) for cid in campaign_ids
    ]
