"""活动看板服务"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, BenefitClaim, Campaign


async def get_campaign_funnel(
    db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID,
) -> dict:
    """获取单个活动的漏斗数据"""
    # 验证活动存在
    campaign = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id)
    )
    if not campaign.scalar_one_or_none():
        return {}

    # 统计各步骤数量
    claims_count = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.campaign_id == campaign_id,
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.claim_type == "claim",
        )
    )
    claims = claims_count.scalar() or 0

    benefits_count = await db.execute(
        select(func.count()).select_from(Benefit).where(
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
    """获取多活动对比数据"""
    results = []
    for cid in campaign_ids:
        claims_count = await db.execute(
            select(func.count()).select_from(BenefitClaim).where(
                BenefitClaim.campaign_id == cid,
                BenefitClaim.tenant_id == tenant_id,
            )
        )
        campaign_name = await db.execute(
            select(Campaign.name).where(Campaign.id == cid, Campaign.tenant_id == tenant_id)
        )
        name = campaign_name.scalar() or "Unknown"
        results.append({
            "campaign_id": str(cid),
            "campaign_name": name,
            "claims": claims_count.scalar() or 0,
        })
    return results
