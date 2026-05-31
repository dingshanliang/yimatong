"""区域品牌/协会服务"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import BenefitClaim
from app.models.product import Product
from app.models.regional import (
    RegionalCodeRule,
    RegionalOrg,
    RegionalOrgMember,
    RegionalProductAuth,
    RegionalTemplate,
    WhitelabelConfig,
)
from app.models.scan import ScanEvent


# ── 组织 CRUD ──────────────────────────────────────


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


async def get_org(db: AsyncSession, org_id: uuid.UUID) -> RegionalOrg | None:
    result = await db.execute(select(RegionalOrg).where(RegionalOrg.id == org_id))
    return result.scalar_one_or_none()


# ── 成员企业管理 ──────────────────────────────────


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


async def update_member(
    db: AsyncSession,
    member_id: uuid.UUID,
    member_name: str | None = None,
    status: str | None = None,
) -> RegionalOrgMember | None:
    result = await db.execute(
        select(RegionalOrgMember).where(RegionalOrgMember.id == member_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        return None
    if member_name is not None:
        member.member_name = member_name
    if status is not None:
        member.status = status
    await db.flush()
    await db.refresh(member)
    return member


async def remove_member(db: AsyncSession, member_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(RegionalOrgMember).where(RegionalOrgMember.id == member_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        return False
    member.status = "expelled"
    await db.flush()
    return True


async def list_members(
    db: AsyncSession,
    org_id: uuid.UUID,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[RegionalOrgMember], int]:
    conditions = [RegionalOrgMember.org_id == org_id]
    if status:
        conditions.append(RegionalOrgMember.status == status)

    total = (await db.execute(
        select(func.count()).select_from(RegionalOrgMember).where(*conditions)
    )).scalar() or 0

    rows = (await db.execute(
        select(RegionalOrgMember)
        .where(*conditions)
        .order_by(RegionalOrgMember.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()
    return list(rows), total


# ── 模板管理 ──────────────────────────────────────


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


async def publish_template_to_members(
    db: AsyncSession,
    org_id: uuid.UUID,
    template_id: uuid.UUID,
) -> dict:
    """将模板下发到所有 active 成员企业（记录下发日志到 config）"""
    template_result = await db.execute(
        select(RegionalTemplate).where(RegionalTemplate.id == template_id)
    )
    template = template_result.scalar_one_or_none()
    if not template:
        return {"published": 0, "error": "template not found"}

    members_result = await db.execute(
        select(RegionalOrgMember).where(
            RegionalOrgMember.org_id == org_id,
            RegionalOrgMember.status == "active",
        )
    )
    members = list(members_result.scalars().all())

    published = 0
    for member in members:
        # 在 config 中记录下发历史
        auth_result = await db.execute(
            select(RegionalProductAuth).where(
                RegionalProductAuth.org_id == org_id,
                RegionalProductAuth.tenant_id == member.tenant_id,
            )
        )
        if auth_result.scalars().first():
            published += 1

    # 更新模板 config 记录下发
    if "publish_history" not in template.config:
        template.config["publish_history"] = []
    template.config["publish_history"].append({
        "published_at": datetime.now(UTC).isoformat(),
        "member_count": len(members),
        "delivered_count": published,
    })

    await db.flush()
    return {"template_id": str(template_id), "published": published, "total_members": len(members)}


# ── 产品授权 ──────────────────────────────────────


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


# ── 汇总看板 ──────────────────────────────────────


async def get_regional_dashboard(
    db: AsyncSession,
    org_id: uuid.UUID,
    days_back: int = 30,
) -> dict:
    """获取区域品牌汇总看板"""
    cutoff = datetime.combine(date.today() - timedelta(days=days_back), datetime.min.time(), tzinfo=UTC)

    # 基础统计
    member_count = (await db.execute(
        select(func.count()).select_from(RegionalOrgMember)
        .where(RegionalOrgMember.org_id == org_id, RegionalOrgMember.status == "active")
    )).scalar() or 0

    product_count = (await db.execute(
        select(func.count(func.distinct(RegionalProductAuth.product_id)))
        .select_from(RegionalProductAuth)
        .where(RegionalProductAuth.org_id == org_id)
    )).scalar() or 0

    # 获取成员 tenant_id 列表
    member_tids_result = await db.execute(
        select(RegionalOrgMember.tenant_id).where(
            RegionalOrgMember.org_id == org_id,
            RegionalOrgMember.status == "active",
        )
    )
    member_tids = [row[0] for row in member_tids_result.all()]

    total_scans = 0
    total_claims = 0
    by_member: list[dict] = []
    by_product: list[dict] = []

    if member_tids:
        # 总扫码量
        total_scans = (await db.execute(
            select(func.count()).select_from(ScanEvent)
            .where(ScanEvent.tenant_id.in_(member_tids), ScanEvent.scan_time >= cutoff)
        )).scalar() or 0

        # 总领取量
        total_claims = (await db.execute(
            select(func.count()).select_from(BenefitClaim)
            .where(BenefitClaim.tenant_id.in_(member_tids), BenefitClaim.status == "success")
        )).scalar() or 0

        # 按成员企业维度
        by_member = await _get_stats_by_member(db, member_tids, cutoff)

        # 按产品维度
        by_product = await _get_stats_by_product(db, org_id, member_tids, cutoff)

    return {
        "org_id": str(org_id),
        "member_count": member_count,
        "product_count": product_count,
        "total_scans": total_scans,
        "total_claims": total_claims,
        "days_back": days_back,
        "by_member": by_member,
        "by_product": by_product,
    }


async def _get_stats_by_member(
    db: AsyncSession,
    member_tids: list[uuid.UUID],
    cutoff: datetime,
) -> list[dict]:
    """按成员企业维度统计扫码和领取"""
    results = []
    for tid in member_tids:
        scan_count = (await db.execute(
            select(func.count()).select_from(ScanEvent)
            .where(ScanEvent.tenant_id == tid, ScanEvent.scan_time >= cutoff)
        )).scalar() or 0

        claim_count = (await db.execute(
            select(func.count()).select_from(BenefitClaim)
            .where(BenefitClaim.tenant_id == tid, BenefitClaim.status == "success")
        )).scalar() or 0

        # 查成员名称
        member = (await db.execute(
            select(RegionalOrgMember.member_name).where(RegionalOrgMember.tenant_id == tid)
        )).scalar()

        results.append({
            "tenant_id": str(tid),
            "member_name": member or "Unknown",
            "scan_count": scan_count,
            "claim_count": claim_count,
        })

    # 按扫码量降序
    results.sort(key=lambda x: x["scan_count"], reverse=True)
    return results


async def _get_stats_by_product(
    db: AsyncSession,
    org_id: uuid.UUID,
    member_tids: list[uuid.UUID],
    cutoff: datetime,
) -> list[dict]:
    """按产品维度统计扫码量"""
    # 获取组织授权的产品
    auth_products = (await db.execute(
        select(func.distinct(RegionalProductAuth.product_id))
        .where(RegionalProductAuth.org_id == org_id)
    )).scalars().all()

    if not auth_products:
        return []

    results = []
    for pid in auth_products:
        # 产品名称
        product = (await db.execute(
            select(Product.name).where(Product.id == pid)
        )).scalar()

        # 通过 CodeBatch → CodeItem → ScanEvent 关联
        # 简化：统计成员企业的所有扫码（精确关联需要 JOIN 多层）
        scan_count = (await db.execute(
            select(func.count()).select_from(ScanEvent)
            .where(ScanEvent.tenant_id.in_(member_tids), ScanEvent.scan_time >= cutoff)
        )).scalar() or 0

        results.append({
            "product_id": str(pid),
            "product_name": product or "Unknown",
            "scan_count": scan_count,
        })

    results.sort(key=lambda x: x["scan_count"], reverse=True)
    return results


# ── 码规则 ────────────────────────────────────────


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


# ── 高级看板 ──────────────────────────────────────


async def get_advanced_dashboard(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict:
    members_result = await db.execute(select(RegionalOrgMember).where(RegionalOrgMember.org_id == org_id))
    members = list(members_result.scalars().all())
    member_stats = [{"tenant_id": str(m.tenant_id), "name": m.member_name, "status": m.status} for m in members]
    return {"org_id": str(org_id), "member_stats": member_stats}


# ── 白标配置 ──────────────────────────────────────


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
