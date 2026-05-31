"""活动与权益服务层"""

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignStatus
from app.models.product import Product


async def create_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    campaign_type: str,
    start_at: str,
    end_at: str,
    rules_json: dict,
    description: str | None = None,
    product_id: uuid.UUID | None = None,
) -> dict:
    product_id = product_id or _product_id_from_rules(rules_json)
    rules_json = _rules_with_product_id(rules_json, product_id)
    c = Campaign(
        tenant_id=tenant_id,
        name=name,
        campaign_type=campaign_type,
        product_id=product_id,
        start_at=start_at,
        end_at=end_at,
        rules_json=rules_json,
        description=description,
    )
    db.add(c)
    await db.flush()
    await db.refresh(c)
    product_names = await _load_product_names(db, tenant_id, [c])
    stats = await _load_campaign_stats(db, tenant_id, [c.id])
    return _campaign_to_dict(c, product_names=product_names, stats=stats)


async def list_campaigns(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: str | None = None,
    campaign_type: str | None = None,
    product_id: uuid.UUID | None = None,
    computed_status: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt = select(Campaign).where(Campaign.tenant_id == tenant_id)
    count_stmt = (
        select(func.count())
        .select_from(Campaign)
        .where(
            Campaign.tenant_id == tenant_id,
        )
    )
    if status:
        stmt = stmt.where(Campaign.status == status)
        count_stmt = count_stmt.where(Campaign.status == status)
    if campaign_type:
        stmt = stmt.where(Campaign.campaign_type == campaign_type)
        count_stmt = count_stmt.where(Campaign.campaign_type == campaign_type)
    if product_id:
        stmt = stmt.where(Campaign.product_id == product_id)
        count_stmt = count_stmt.where(Campaign.product_id == product_id)
    if computed_status:
        status_conditions = _computed_status_conditions(computed_status)
        if status_conditions is not None:
            stmt = stmt.where(status_conditions)
            count_stmt = count_stmt.where(status_conditions)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Campaign.name.ilike(pattern))
        count_stmt = count_stmt.where(Campaign.name.ilike(pattern))

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(Campaign.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    campaigns = result.scalars().all()
    product_names = await _load_product_names(db, tenant_id, campaigns)
    stats = await _load_campaign_stats(db, tenant_id, [c.id for c in campaigns])
    return [_campaign_to_dict(c, product_names=product_names, stats=stats) for c in campaigns], total


async def get_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id),
    )
    c = result.scalar_one_or_none()
    if not c:
        return None
    product_names = await _load_product_names(db, tenant_id, [c])
    stats = await _load_campaign_stats(db, tenant_id, [c.id])
    return _campaign_to_dict(c, product_names=product_names, stats=stats)


async def update_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    **fields,
) -> dict | None:
    result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id),
    )
    c = result.scalar_one_or_none()
    if not c:
        return None
    for k, v in fields.items():
        if k == "rules_json" and isinstance(v, dict):
            v = _rules_with_product_id(v, fields.get("product_id", c.product_id))
        if k == "product_id":
            c.product_id = v
            continue
        if v is not None:
            setattr(c, k, v)
    if "product_id" in fields:
        c.rules_json = _rules_with_product_id(c.rules_json, c.product_id)
    await db.flush()
    await db.refresh(c)
    product_names = await _load_product_names(db, tenant_id, [c])
    stats = await _load_campaign_stats(db, tenant_id, [c.id])
    return _campaign_to_dict(c, product_names=product_names, stats=stats)


async def change_campaign_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    new_status: str,
) -> dict | None:
    result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id),
    )
    c = result.scalar_one_or_none()
    if not c:
        return None
    c.status = new_status
    await db.flush()
    await db.refresh(c)
    event_name = "campaign.started" if new_status in ("ACTIVE", "active") else "campaign.ended"
    await event_bus.emit(
        event_name,
        {"campaign_id": str(campaign_id), "status": new_status},
        str(tenant_id),
    )
    product_names = await _load_product_names(db, tenant_id, [c])
    stats = await _load_campaign_stats(db, tenant_id, [c.id])
    return _campaign_to_dict(c, product_names=product_names, stats=stats)


async def delete_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.tenant_id == tenant_id,
            Campaign.status == CampaignStatus.DRAFT,
        ),
    )
    c = result.scalar_one_or_none()
    if not c:
        return False
    await db.delete(c)
    await db.flush()
    return True


# --- Benefits ---


async def create_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    name: str,
    benefit_type: str,
    config_json: dict,
    stock_total: int,
    per_person_limit: int = 1,
    connector_id: uuid.UUID | None = None,
) -> dict:
    b = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        name=name,
        benefit_type=benefit_type,
        config_json=config_json,
        stock_total=stock_total,
        per_person_limit=per_person_limit,
        connector_id=connector_id,
    )
    db.add(b)
    await db.flush()
    await db.refresh(b)
    return _benefit_to_dict(b)


async def list_benefits(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> list[dict]:
    result = await db.execute(
        select(Benefit)
        .where(
            Benefit.tenant_id == tenant_id,
            Benefit.campaign_id == campaign_id,
        )
        .order_by(Benefit.id.desc())
    )
    return [_benefit_to_dict(b) for b in result.scalars().all()]


async def list_all_benefits(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt = select(Benefit).where(Benefit.tenant_id == tenant_id)
    count_stmt = (
        select(func.count())
        .select_from(Benefit)
        .where(
            Benefit.tenant_id == tenant_id,
        )
    )
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(Benefit.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return [_benefit_to_dict(b) for b in result.scalars().all()], total


async def get_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tenant_id),
    )
    b = result.scalar_one_or_none()
    return _benefit_to_dict(b) if b else None


async def update_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
    **fields,
) -> dict | None:
    result = await db.execute(
        select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tenant_id),
    )
    b = result.scalar_one_or_none()
    if not b:
        return None
    for k, v in fields.items():
        if v is not None:
            setattr(b, k, v)
    await db.flush()
    await db.refresh(b)
    return _benefit_to_dict(b)


async def delete_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tenant_id),
    )
    b = result.scalar_one_or_none()
    if not b:
        return False
    await db.delete(b)
    await db.flush()
    return True


async def list_benefit_claims_admin(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt = select(BenefitClaim).where(BenefitClaim.tenant_id == tenant_id)
    count_stmt = (
        select(func.count())
        .select_from(BenefitClaim)
        .where(
            BenefitClaim.tenant_id == tenant_id,
        )
    )
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(BenefitClaim.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return [_claim_to_dict(c) for c in result.scalars().all()], total


async def claim_benefit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    benefit_id: uuid.UUID,
    consumer_id: str,
    idempotency_key: str,
) -> dict:
    """领取权益，带幂等控制和库存校验"""
    # 幂等检查
    existing = await db.execute(
        select(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit_id,
            BenefitClaim.consumer_id == consumer_id,
            BenefitClaim.idempotency_key == idempotency_key,
        )
    )
    existing_claim = existing.scalar_one_or_none()
    if existing_claim:
        return {"status": "idempotent", "claim": _claim_to_dict(existing_claim)}

    # 查询权益
    benefit_result = await db.execute(
        select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tenant_id),
    )
    benefit = benefit_result.scalar_one_or_none()
    if not benefit:
        return {"status": "not_found"}

    # 库存检查
    if benefit.stock_used >= benefit.stock_total:
        return {"status": "out_of_stock"}

    # 每人限额检查
    count_result = await db.execute(
        select(func.count())
        .select_from(BenefitClaim)
        .where(
            BenefitClaim.benefit_id == benefit_id,
            BenefitClaim.consumer_id == consumer_id,
        )
    )
    claimed_count = count_result.scalar() or 0
    if claimed_count >= benefit.per_person_limit:
        return {"status": "limit_reached"}

    # 扣减库存
    await db.execute(update(Benefit).where(Benefit.id == benefit_id).values(stock_used=Benefit.stock_used + 1))

    # 创建领取记录
    claim = BenefitClaim(
        tenant_id=tenant_id,
        benefit_id=benefit_id,
        campaign_id=benefit.campaign_id,
        consumer_id=consumer_id,
        idempotency_key=idempotency_key,
    )
    db.add(claim)
    await db.flush()
    await db.refresh(claim)

    await event_bus.emit(
        "claim.created",
        {"claim_id": str(claim.id), "benefit_id": str(benefit_id), "consumer_id": consumer_id},
        str(tenant_id),
    )
    return {"status": "success", "claim": _claim_to_dict(claim)}


def _claim_to_dict(c: BenefitClaim) -> dict:
    return {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "benefit_id": str(c.benefit_id),
        "campaign_id": str(c.campaign_id),
        "consumer_id": c.consumer_id,
        "claim_type": c.claim_type,
        "status": c.status,
        "delivery_status": c.delivery_status,
    }


async def campaign_product_exists(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> bool:
    result = await db.execute(select(Product.id).where(Product.id == product_id, Product.tenant_id == tenant_id))
    return result.scalar_one_or_none() is not None


def _rules_with_product_id(rules_json: dict | None, product_id: uuid.UUID | None) -> dict:
    rules = dict(rules_json or {})
    if product_id:
        rules["product_id"] = str(product_id)
    else:
        rules.pop("product_id", None)
    return rules


def _product_id_from_rules(rules_json: dict | None) -> uuid.UUID | None:
    raw = (rules_json or {}).get("product_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _campaign_product_id(c: Campaign) -> uuid.UUID | None:
    if c.product_id:
        return c.product_id
    return _product_id_from_rules(c.rules_json)


async def _load_product_names(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaigns: list[Campaign],
) -> dict[uuid.UUID, str]:
    product_ids = {pid for c in campaigns if (pid := _campaign_product_id(c))}
    if not product_ids:
        return {}
    result = await db.execute(
        select(Product.id, Product.name).where(Product.tenant_id == tenant_id, Product.id.in_(product_ids))
    )
    return {row.id: row.name for row in result.all()}


async def _load_campaign_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    if not campaign_ids:
        return {}
    stats: dict[uuid.UUID, dict] = {
        cid: {"benefit_count": 0, "stock_total": 0, "stock_used": 0, "claim_count": 0}
        for cid in campaign_ids
    }
    benefit_result = await db.execute(
        select(
            Benefit.campaign_id,
            func.count(Benefit.id).label("benefit_count"),
            func.coalesce(func.sum(Benefit.stock_total), 0).label("stock_total"),
            func.coalesce(func.sum(Benefit.stock_used), 0).label("stock_used"),
        )
        .where(Benefit.tenant_id == tenant_id, Benefit.campaign_id.in_(campaign_ids))
        .group_by(Benefit.campaign_id)
    )
    for row in benefit_result.all():
        stats[row.campaign_id].update(
            {
                "benefit_count": row.benefit_count or 0,
                "stock_total": row.stock_total or 0,
                "stock_used": row.stock_used or 0,
            }
        )

    claim_result = await db.execute(
        select(BenefitClaim.campaign_id, func.count(BenefitClaim.id).label("claim_count"))
        .where(BenefitClaim.tenant_id == tenant_id, BenefitClaim.campaign_id.in_(campaign_ids))
        .group_by(BenefitClaim.campaign_id)
    )
    for row in claim_result.all():
        stats[row.campaign_id]["claim_count"] = row.claim_count or 0
    return stats


def _parse_campaign_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compute_campaign_status(c: Campaign) -> str:
    if c.status in (CampaignStatus.DRAFT, CampaignStatus.PAUSED, CampaignStatus.ENDED):
        return c.status
    start_at = _parse_campaign_datetime(c.start_at)
    end_at = _parse_campaign_datetime(c.end_at)
    now = datetime.now(end_at.tzinfo) if end_at and end_at.tzinfo else datetime.now()
    if end_at and end_at < now:
        return CampaignStatus.ENDED
    if start_at:
        start_now = datetime.now(start_at.tzinfo) if start_at.tzinfo else datetime.now()
        if start_at > start_now:
            return "pending"
    return CampaignStatus.ACTIVE


def _computed_status_conditions(computed_status: str):
    now = datetime.now().isoformat(timespec="seconds")
    if computed_status == CampaignStatus.DRAFT:
        return Campaign.status == CampaignStatus.DRAFT
    if computed_status == CampaignStatus.PAUSED:
        return Campaign.status == CampaignStatus.PAUSED
    if computed_status == "pending":
        return (Campaign.status == CampaignStatus.ACTIVE) & (Campaign.start_at > now)
    if computed_status == CampaignStatus.ACTIVE:
        return (Campaign.status == CampaignStatus.ACTIVE) & (Campaign.start_at <= now) & (Campaign.end_at >= now)
    if computed_status == CampaignStatus.ENDED:
        return or_(
            Campaign.status == CampaignStatus.ENDED,
            (Campaign.status == CampaignStatus.ACTIVE) & (Campaign.end_at < now),
        )
    return None


def _campaign_to_dict(
    c: Campaign,
    product_names: dict[uuid.UUID, str] | None = None,
    stats: dict[uuid.UUID, dict] | None = None,
) -> dict:
    product_id = _campaign_product_id(c)
    campaign_stats = (stats or {}).get(c.id, {})
    return {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "name": c.name,
        "campaign_type": c.campaign_type,
        "status": c.status,
        "computed_status": _compute_campaign_status(c),
        "product_id": str(product_id) if product_id else None,
        "product_name": (product_names or {}).get(product_id) if product_id else None,
        "start_at": c.start_at,
        "end_at": c.end_at,
        "rules_json": c.rules_json,
        "description": c.description,
        "benefit_count": campaign_stats.get("benefit_count", 0),
        "stock_total": campaign_stats.get("stock_total", 0),
        "stock_used": campaign_stats.get("stock_used", 0),
        "claim_count": campaign_stats.get("claim_count", 0),
    }


def _benefit_to_dict(b: Benefit) -> dict:
    return {
        "id": str(b.id),
        "tenant_id": str(b.tenant_id),
        "campaign_id": str(b.campaign_id),
        "name": b.name,
        "benefit_type": b.benefit_type,
        "config_json": b.config_json,
        "connector_id": str(b.connector_id) if b.connector_id else None,
        "stock_total": b.stock_total,
        "stock_used": b.stock_used,
        "per_person_limit": b.per_person_limit,
        "status": b.status,
    }
