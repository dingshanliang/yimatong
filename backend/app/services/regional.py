"""区域品牌/协会服务"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.regional import (
    RegionalCodeRule,
    RegionalOrg,
    RegionalOrgMember,
    RegionalProductAuth,
    RegionalTemplate,
    WhitelabelConfig,
)
from app.models.scan import ScanEvent


async def create_regional_org(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    org_type: str,
) -> RegionalOrg:
    org = RegionalOrg(tenant_id=tenant_id, name=name, org_type=org_type)
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def list_regional_orgs(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[RegionalOrg]:
    result = await db.execute(
        select(RegionalOrg).where(RegionalOrg.tenant_id == tenant_id).order_by(RegionalOrg.id.desc())
    )
    return list(result.scalars().all())


async def add_member(
    db: AsyncSession,
    org_id: uuid.UUID,
    tenant_id: uuid.UUID,
    member_name: str,
) -> RegionalOrgMember:
    member = RegionalOrgMember(
        org_id=org_id,
        tenant_id=tenant_id,
        member_name=member_name,
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return member


async def list_members(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[RegionalOrgMember]:
    result = await db.execute(
        select(RegionalOrgMember).where(RegionalOrgMember.org_id == org_id).order_by(RegionalOrgMember.id.desc())
    )
    return list(result.scalars().all())


async def create_shared_template(
    db: AsyncSession,
    org_id: uuid.UUID,
    name: str,
    config: dict,
) -> RegionalTemplate:
    template = RegionalTemplate(org_id=org_id, name=name, config=config)
    db.add(template)
    await db.flush()
    await db.refresh(template)
    return template


async def list_shared_templates(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[RegionalTemplate]:
    result = await db.execute(
        select(RegionalTemplate).where(RegionalTemplate.org_id == org_id).order_by(RegionalTemplate.id.desc())
    )
    return list(result.scalars().all())


async def authorize_product(
    db: AsyncSession,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> RegionalProductAuth:
    auth = RegionalProductAuth(org_id=org_id, product_id=product_id, tenant_id=tenant_id)
    db.add(auth)
    await db.flush()
    await db.refresh(auth)
    return auth


async def get_regional_dashboard(
    db: AsyncSession,
    org_id: uuid.UUID,
    days_back: int = 30,
) -> dict:
    """获取区域品牌汇总看板"""
    from datetime import UTC, date, datetime, timedelta

    members_result = await db.execute(
        select(func.count()).select_from(RegionalOrgMember).where(RegionalOrgMember.org_id == org_id)
    )
    member_count = members_result.scalar() or 0

    member_tids_result = await db.execute(select(RegionalOrgMember.tenant_id).where(RegionalOrgMember.org_id == org_id))
    member_tids = [row[0] for row in member_tids_result.all()]

    total_scans = 0
    if member_tids:
        cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)
        scans_result = await db.execute(
            select(func.count())
            .select_from(ScanEvent)
            .where(
                ScanEvent.tenant_id.in_(member_tids),
                ScanEvent.scan_time >= cutoff,
            )
        )
        total_scans = scans_result.scalar() or 0

    return {
        "org_id": str(org_id),
        "member_count": member_count,
        "total_scans": total_scans,
    }


async def create_code_rule(
    db: AsyncSession,
    org_id: uuid.UUID,
    rule_name: str,
    pattern: str,
    prefix: str,
) -> RegionalCodeRule:
    rule = RegionalCodeRule(org_id=org_id, rule_name=rule_name, pattern=pattern, prefix=prefix)
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


async def list_code_rules(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[RegionalCodeRule]:
    result = await db.execute(
        select(RegionalCodeRule).where(RegionalCodeRule.org_id == org_id).order_by(RegionalCodeRule.id.desc())
    )
    return list(result.scalars().all())


async def get_advanced_dashboard(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict:
    members_result = await db.execute(select(RegionalOrgMember).where(RegionalOrgMember.org_id == org_id))
    members = list(members_result.scalars().all())
    member_stats = [{"tenant_id": str(m.tenant_id), "name": m.member_name, "status": m.status} for m in members]
    return {"org_id": str(org_id), "member_stats": member_stats}


async def set_whitelabel(
    db: AsyncSession,
    org_id: uuid.UUID,
    brand_name: str,
    hide_yimatong: bool,
    primary_color: str = "#000000",
) -> WhitelabelConfig:
    result = await db.execute(select(WhitelabelConfig).where(WhitelabelConfig.org_id == org_id))
    config = result.scalar_one_or_none()
    if config:
        config.brand_name = brand_name
        config.hide_yimatong = hide_yimatong
        config.primary_color = primary_color
    else:
        config = WhitelabelConfig(
            org_id=org_id,
            brand_name=brand_name,
            hide_yimatong=hide_yimatong,
            primary_color=primary_color,
        )
        db.add(config)
    await db.flush()
    await db.refresh(config)
    return config


async def get_whitelabel(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> WhitelabelConfig | None:
    result = await db.execute(select(WhitelabelConfig).where(WhitelabelConfig.org_id == org_id))
    return result.scalar_one_or_none()
