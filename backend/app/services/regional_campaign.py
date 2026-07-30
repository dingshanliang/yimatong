"""区域品牌统一营销活动管理。

品牌方/协会创建统一活动，指定参与的成员企业。
活动数据存储在 RegionalOrg.config JSON 中。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regional import RegionalOrg, RegionalOrgMember


async def create_unified_campaign(
    db: AsyncSession,
    org_id: uuid.UUID,
    tenant_id: uuid.UUID,
    name: str,
    description: str | None = None,
    member_ids: list[str] | None = None,
) -> dict:
    """创建统一营销活动。"""
    org_result = await db.execute(select(RegionalOrg).where(RegionalOrg.id == org_id))
    org = org_result.scalar_one_or_none()
    if not org:
        raise ValueError("Organization not found")

    # 获取参与成员
    if member_ids:
        target_member_ids = [uuid.UUID(mid) for mid in member_ids]
    else:
        # 全部 active 成员
        members_result = await db.execute(
            select(RegionalOrgMember).where(RegionalOrgMember.org_id == org_id, RegionalOrgMember.status == "active")
        )
        target_member_ids = [m.id for m in members_result.scalars().all()]

    campaign_id = uuid.uuid4()
    campaign = {
        "id": str(campaign_id),
        "name": name,
        "description": description,
        "org_id": str(org_id),
        "created_by_tenant": str(tenant_id),
        "member_count": len(target_member_ids),
        "member_ids": [str(mid) for mid in target_member_ids],
        "status": "draft",
        "created_at": datetime.now(UTC).isoformat(),
    }

    campaigns = org.config.get("unified_campaigns", [])
    campaigns.insert(0, campaign)
    org.config["unified_campaigns"] = campaigns
    await db.flush()

    return campaign


async def list_unified_campaigns(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[dict]:
    """列出组织的统一营销活动。"""
    org_result = await db.execute(select(RegionalOrg).where(RegionalOrg.id == org_id))
    org = org_result.scalar_one_or_none()
    if not org:
        return []
    return org.config.get("unified_campaigns", [])
