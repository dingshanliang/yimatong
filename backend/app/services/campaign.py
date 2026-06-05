"""活动与权益服务层"""

import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_bus import event_bus
from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignStatus
from app.models.connector import BenefitDelivery
from app.models.product import Product

CAMPAIGN_GOALS = {
    "first_scan_coupon",
    "lottery",
    "points",
    "private_domain_repurchase",
    "festival",
    "custom",
}

PARTICIPATION_CONDITION_TYPES = {
    "first_scan",
    "any_scan",
    "member_only",
}

BENEFIT_VALIDITY_TYPES = {
    "campaign_period",
    "after_claim_days",
    "fixed_range",
}

WECOM_MODES = {"none", "guide", "required"}
BENEFIT_TYPES = {
    "platform_coupon",
    "external_link",
    "private_domain",
    "form_benefit",
    "cash_red_packet",
}
BENEFIT_STATUSES = {"active", "inactive"}
CASH_RED_PACKET_AMOUNT_TYPES = {"fixed", "random", "lucky"}


def validate_campaign_rules_shape(rules_json: dict) -> dict:
    campaign_goal = rules_json.get("campaign_goal")
    if campaign_goal is not None and campaign_goal not in CAMPAIGN_GOALS:
        raise ValueError(f"campaign_goal must be one of: {', '.join(sorted(CAMPAIGN_GOALS))}")

    participation_type = rules_json.get("participation_condition_type")
    if participation_type is not None and participation_type not in PARTICIPATION_CONDITION_TYPES:
        raise ValueError(
            f"participation_condition_type must be one of: {', '.join(sorted(PARTICIPATION_CONDITION_TYPES))}"
        )

    wecom_mode = rules_json.get("wecom_mode", "none")
    if wecom_mode is not None and wecom_mode not in WECOM_MODES:
        raise ValueError(f"wecom_mode must be one of: {', '.join(sorted(WECOM_MODES))}")

    claim_limit_count = rules_json.get("claim_limit_count")
    if claim_limit_count is not None:
        if not isinstance(claim_limit_count, int) or claim_limit_count < 1:
            raise ValueError("claim_limit_count must be an integer greater than or equal to 1")

    return rules_json


def validate_benefit_config_shape(config_json: dict, benefit_type: str | None = None) -> dict:
    if benefit_type is not None and benefit_type not in BENEFIT_TYPES:
        raise ValueError(f"benefit_type must be one of: {', '.join(sorted(BENEFIT_TYPES))}")

    campaign_goal = config_json.get("campaign_goal")
    if campaign_goal is not None and campaign_goal not in CAMPAIGN_GOALS:
        raise ValueError(f"campaign_goal must be one of: {', '.join(sorted(CAMPAIGN_GOALS))}")

    validity_type = config_json.get("validity_type")
    if validity_type is not None and validity_type not in BENEFIT_VALIDITY_TYPES:
        raise ValueError(f"validity_type must be one of: {', '.join(sorted(BENEFIT_VALIDITY_TYPES))}")

    if validity_type == "after_claim_days":
        validity_days = config_json.get("validity_days")
        if not isinstance(validity_days, int) or validity_days < 1:
            raise ValueError("validity_days must be an integer greater than or equal to 1")

    if validity_type == "fixed_range":
        start_at = _parse_config_datetime(config_json.get("validity_start_at"), "validity_start_at")
        end_at = _parse_config_datetime(config_json.get("validity_end_at"), "validity_end_at")
        if end_at < start_at:
            raise ValueError("validity_end_at must be later than or equal to validity_start_at")

    if benefit_type == "platform_coupon":
        amount = config_json.get("amount")
        if amount is not None and (not isinstance(amount, int | float) or amount < 0):
            raise ValueError("amount must be a number greater than or equal to 0")
        min_order = config_json.get("min_order")
        if min_order is not None and (not isinstance(min_order, int | float) or min_order < 0):
            raise ValueError("min_order must be a number greater than or equal to 0")

    if benefit_type == "external_link":
        _require_url(config_json, "url")

    if benefit_type == "private_domain":
        _require_url(config_json, "qr_image_url")

    if benefit_type == "form_benefit":
        _require_url(config_json, "form_url")
        if "require_phone" in config_json and not isinstance(config_json["require_phone"], bool):
            raise ValueError("require_phone must be a boolean")

    if benefit_type == "cash_red_packet":
        amount_type = config_json.get("amount_type")
        if amount_type not in CASH_RED_PACKET_AMOUNT_TYPES:
            raise ValueError(f"amount_type must be one of: {', '.join(sorted(CASH_RED_PACKET_AMOUNT_TYPES))}")
        _require_positive_number(config_json, "budget")
        if amount_type == "fixed":
            _require_positive_number(config_json, "fixed_amount", max_value=20000)
        if amount_type == "random":
            min_amount = _require_positive_number(config_json, "min_amount", max_value=20000)
            max_amount = _require_positive_number(config_json, "max_amount", max_value=20000)
            if max_amount < min_amount:
                raise ValueError("max_amount must be greater than or equal to min_amount")
        if amount_type == "lucky":
            _require_positive_number(config_json, "lucky_min_per", max_value=20000)
            total_count = config_json.get("lucky_total_count")
            if not isinstance(total_count, int) or total_count < 2:
                raise ValueError("lucky_total_count must be an integer greater than or equal to 2")

    return config_json


def _require_url(config_json: dict, field_name: str) -> str:
    value = config_json.get(field_name)
    if not isinstance(value, str) or not value.strip().lower().startswith(("http://", "https://")):
        raise ValueError(f"{field_name} must be an http(s) URL")
    return value.strip()


def _require_positive_number(config_json: dict, field_name: str, max_value: int | None = None) -> int | float:
    value = config_json.get(field_name)
    if not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{field_name} must be a number greater than 0")
    if max_value is not None and value > max_value:
        raise ValueError(f"{field_name} must be less than or equal to {max_value}")
    return value


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


async def list_brand_campaigns(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    from app.models.product import Product

    product_ids_result = await db.execute(
        select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
    )
    product_ids = list(product_ids_result.scalars().all())

    if not product_ids:
        return [], 0

    stmt = select(Campaign).where(
        Campaign.tenant_id == tenant_id,
        Campaign.product_id.in_(product_ids),
    )
    count_stmt = select(func.count()).select_from(Campaign).where(
        Campaign.tenant_id == tenant_id,
        Campaign.product_id.in_(product_ids),
    )

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


async def get_campaign_activation_blockers(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
) -> list[str] | None:
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))
    campaign = result.scalar_one_or_none()
    if not campaign:
        return None

    blockers: list[str] = []
    if not _campaign_product_id(campaign):
        blockers.append("活动未关联产品")

    start_at = _parse_campaign_datetime(campaign.start_at)
    end_at = _parse_campaign_datetime(campaign.end_at)
    if not start_at or not end_at:
        blockers.append("投放时间不完整")
    elif end_at < start_at:
        blockers.append("结束时间不能早于开始时间")

    stats = await _load_campaign_stats(db, tenant_id, [campaign.id])
    campaign_stats = stats.get(campaign.id, {})
    if campaign_stats.get("benefit_count", 0) < 1:
        blockers.append("活动未配置权益")
    if campaign_stats.get("stock_total", 0) < 1:
        blockers.append("权益库存为 0")

    if (campaign.rules_json or {}).get("wecom_mode") == "required":
        from app.services.wecom_integration import get_active_wecom_connector

        connector = await get_active_wecom_connector(db, tenant_id)
        if not connector or connector.config.get("status") != "connected":
            blockers.append("请先完成企业微信连接，再上线加企微后领取活动")

    return blockers


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
    campaign_id: uuid.UUID | None,
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


async def attach_benefit_to_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    benefit_id: uuid.UUID,
) -> dict | None:
    campaign_result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id),
    )
    if not campaign_result.scalar_one_or_none():
        return None

    result = await db.execute(
        select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tenant_id),
    )
    benefit = result.scalar_one_or_none()
    if not benefit:
        return None
    if benefit.campaign_id and benefit.campaign_id != campaign_id:
        raise ValueError("Benefit already used by another campaign")

    benefit.campaign_id = campaign_id
    await db.flush()
    await db.refresh(benefit)
    return _benefit_to_dict(benefit)


async def detach_benefit_from_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    benefit_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(Benefit).where(
            Benefit.id == benefit_id,
            Benefit.tenant_id == tenant_id,
            Benefit.campaign_id == campaign_id,
        ),
    )
    benefit = result.scalar_one_or_none()
    if not benefit:
        return None

    claim_count = await _benefit_claim_count(db, tenant_id, benefit_id)
    if claim_count > 0:
        raise ValueError("Benefit already has claims and cannot be detached")

    benefit.campaign_id = None
    await db.flush()
    await db.refresh(benefit)
    return _benefit_to_dict(benefit)


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
    q: str | None = None,
    benefit_type: str | None = None,
    status: str | None = None,
    campaign_id: uuid.UUID | None = None,
    usage: str | None = None,
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
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Benefit.name.ilike(pattern))
        count_stmt = count_stmt.where(Benefit.name.ilike(pattern))
    if benefit_type:
        stmt = stmt.where(Benefit.benefit_type == benefit_type)
        count_stmt = count_stmt.where(Benefit.benefit_type == benefit_type)
    if status:
        stmt = stmt.where(Benefit.status == status)
        count_stmt = count_stmt.where(Benefit.status == status)
    if campaign_id:
        stmt = stmt.where(Benefit.campaign_id == campaign_id)
        count_stmt = count_stmt.where(Benefit.campaign_id == campaign_id)
    if usage == "unused":
        stmt = stmt.where(Benefit.campaign_id.is_(None))
        count_stmt = count_stmt.where(Benefit.campaign_id.is_(None))
    if usage == "used":
        stmt = stmt.where(Benefit.campaign_id.is_not(None))
        count_stmt = count_stmt.where(Benefit.campaign_id.is_not(None))
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(Benefit.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return [_benefit_to_dict(b) for b in result.scalars().all()], total


async def get_benefit_summary(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    total_result = await db.execute(select(func.count()).select_from(Benefit).where(Benefit.tenant_id == tenant_id))
    active_result = await db.execute(
        select(func.count()).select_from(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.status == "active")
    )
    unused_result = await db.execute(
        select(func.count()).select_from(Benefit).where(Benefit.tenant_id == tenant_id, Benefit.campaign_id.is_(None))
    )
    stock_result = await db.execute(
        select(func.coalesce(func.sum(Benefit.stock_total), 0), func.coalesce(func.sum(Benefit.stock_used), 0)).where(
            Benefit.tenant_id == tenant_id
        )
    )
    claim_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(BenefitClaim.tenant_id == tenant_id)
    )
    failed_delivery_result = await db.execute(
        select(func.count())
        .select_from(BenefitClaim)
        .where(BenefitClaim.tenant_id == tenant_id, BenefitClaim.delivery_status == "failed")
    )
    stock_total, stock_used = stock_result.one()
    return {
        "total": total_result.scalar() or 0,
        "active": active_result.scalar() or 0,
        "unused": unused_result.scalar() or 0,
        "stock_total": stock_total or 0,
        "stock_used": stock_used or 0,
        "stock_remaining": max((stock_total or 0) - (stock_used or 0), 0),
        "claim_count": claim_result.scalar() or 0,
        "failed_delivery_count": failed_delivery_result.scalar() or 0,
    }


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
    next_type = fields.get("benefit_type", b.benefit_type)
    next_config = fields.get("config_json", b.config_json)
    validate_benefit_config_shape(next_config, next_type)
    if fields.get("status") is not None and fields["status"] not in BENEFIT_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(BENEFIT_STATUSES))}")
    if fields.get("stock_total") is not None and fields["stock_total"] < b.stock_used:
        raise ValueError("stock_total cannot be less than stock_used")
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
) -> str | None:
    result = await db.execute(
        select(Benefit).where(Benefit.id == benefit_id, Benefit.tenant_id == tenant_id),
    )
    b = result.scalar_one_or_none()
    if not b:
        return None
    if b.campaign_id or await _benefit_claim_count(db, tenant_id, benefit_id) > 0:
        return "in_use"
    await db.delete(b)
    await db.flush()
    return "deleted"


async def list_benefit_claims_admin(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    q: str | None = None,
    benefit_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    status: str | None = None,
    delivery_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    latest_delivery_match = or_(
        BenefitDelivery.claim_id == BenefitClaim.id,
        and_(
            BenefitDelivery.claim_id.is_(None),
            BenefitDelivery.benefit_id == BenefitClaim.benefit_id,
            BenefitDelivery.consumer_id == BenefitClaim.consumer_id,
        ),
    )
    latest_delivery_id = (
        select(BenefitDelivery.id)
        .where(BenefitDelivery.tenant_id == tenant_id, latest_delivery_match)
        .order_by(BenefitDelivery.created_at.desc().nulls_last(), BenefitDelivery.id.desc())
        .limit(1)
        .correlate(BenefitClaim)
        .scalar_subquery()
    )
    latest_delivery_status = (
        select(BenefitDelivery.status)
        .where(BenefitDelivery.tenant_id == tenant_id, latest_delivery_match)
        .order_by(BenefitDelivery.created_at.desc().nulls_last(), BenefitDelivery.id.desc())
        .limit(1)
        .correlate(BenefitClaim)
        .scalar_subquery()
    )
    latest_delivery_retry_count = (
        select(BenefitDelivery.retry_count)
        .where(BenefitDelivery.tenant_id == tenant_id, latest_delivery_match)
        .order_by(BenefitDelivery.created_at.desc().nulls_last(), BenefitDelivery.id.desc())
        .limit(1)
        .correlate(BenefitClaim)
        .scalar_subquery()
    )
    latest_delivery_next_retry_at = (
        select(BenefitDelivery.next_retry_at)
        .where(BenefitDelivery.tenant_id == tenant_id, latest_delivery_match)
        .order_by(BenefitDelivery.created_at.desc().nulls_last(), BenefitDelivery.id.desc())
        .limit(1)
        .correlate(BenefitClaim)
        .scalar_subquery()
    )
    filters = [BenefitClaim.tenant_id == tenant_id]
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(BenefitClaim.consumer_id.ilike(pattern), Benefit.name.ilike(pattern), Campaign.name.ilike(pattern))
        )
    if benefit_id:
        filters.append(BenefitClaim.benefit_id == benefit_id)
    if campaign_id:
        filters.append(BenefitClaim.campaign_id == campaign_id)
    if status:
        filters.append(BenefitClaim.status == _claim_status_to_storage(status))
    if delivery_status:
        filters.append(BenefitClaim.delivery_status == delivery_status)

    count_stmt = (
        select(func.count())
        .select_from(BenefitClaim)
        .join(Benefit, Benefit.id == BenefitClaim.benefit_id)
        .outerjoin(Campaign, Campaign.id == BenefitClaim.campaign_id)
        .where(*filters)
    )
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = (
        select(
            BenefitClaim,
            Benefit.name.label("benefit_name"),
            Campaign.name.label("campaign_name"),
            latest_delivery_id.label("latest_delivery_id"),
            latest_delivery_status.label("latest_delivery_status"),
            latest_delivery_retry_count.label("delivery_retry_count"),
            latest_delivery_next_retry_at.label("delivery_next_retry_at"),
        )
        .join(Benefit, Benefit.id == BenefitClaim.benefit_id)
        .outerjoin(Campaign, Campaign.id == BenefitClaim.campaign_id)
        .where(*filters)
        .order_by(BenefitClaim.created_at.desc().nulls_last(), BenefitClaim.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return [
        _claim_to_dict(
            claim,
            benefit_name=benefit_name,
            campaign_name=campaign_name,
            latest_delivery_id=latest_delivery_id,
            latest_delivery_status=latest_delivery_status,
            delivery_retry_count=delivery_retry_count,
            delivery_next_retry_at=delivery_next_retry_at,
        )
        for (
            claim,
            benefit_name,
            campaign_name,
            latest_delivery_id,
            latest_delivery_status,
            delivery_retry_count,
            delivery_next_retry_at,
        ) in result.all()
    ], total


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
    if benefit.status != "active":
        return {"status": "inactive"}

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


async def _benefit_claim_count(db: AsyncSession, tenant_id: uuid.UUID, benefit_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.benefit_id == benefit_id,
        )
    )
    return result.scalar() or 0


def _claim_status_to_storage(status: str) -> str:
    return "success" if status == "claimed" else status


def _claim_status_to_public(status: str) -> str:
    return "claimed" if status == "success" else status


def _claim_to_dict(
    c: BenefitClaim,
    benefit_name: str | None = None,
    campaign_name: str | None = None,
    latest_delivery_id: uuid.UUID | None = None,
    latest_delivery_status: str | None = None,
    delivery_retry_count: int | None = None,
    delivery_next_retry_at: datetime | None = None,
) -> dict:
    return {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "benefit_id": str(c.benefit_id),
        "benefit_name": benefit_name,
        "campaign_id": str(c.campaign_id) if c.campaign_id else None,
        "campaign_name": campaign_name,
        "consumer_id": c.consumer_id,
        "claim_type": c.claim_type,
        "status": _claim_status_to_public(c.status),
        "delivery_status": c.delivery_status,
        "claimed_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "latest_delivery_id": str(latest_delivery_id) if latest_delivery_id else None,
        "latest_delivery_status": latest_delivery_status,
        "delivery_retry_count": delivery_retry_count,
        "delivery_next_retry_at": delivery_next_retry_at.isoformat() if delivery_next_retry_at else None,
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
        cid: {
            "benefit_count": 0,
            "stock_total": 0,
            "stock_used": 0,
            "claim_count": 0,
            "wecom_add_count": 0,
        }
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

    from app.models.wecom import WeComExternalContact, WeComExternalContactStatus

    wecom_result = await db.execute(
        select(WeComExternalContact.campaign_id, func.count(WeComExternalContact.id).label("wecom_add_count"))
        .where(
            WeComExternalContact.tenant_id == tenant_id,
            WeComExternalContact.campaign_id.in_(campaign_ids),
            WeComExternalContact.status == WeComExternalContactStatus.ACTIVE,
        )
        .group_by(WeComExternalContact.campaign_id)
    )
    for row in wecom_result.all():
        stats[row.campaign_id]["wecom_add_count"] = row.wecom_add_count or 0
    return stats


def _parse_config_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid datetime") from exc


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
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "benefit_count": campaign_stats.get("benefit_count", 0),
        "stock_total": campaign_stats.get("stock_total", 0),
        "stock_used": campaign_stats.get("stock_used", 0),
        "claim_count": campaign_stats.get("claim_count", 0),
        "wecom_add_count": campaign_stats.get("wecom_add_count", 0),
    }


def _benefit_to_dict(b: Benefit) -> dict:
    return {
        "id": str(b.id),
        "tenant_id": str(b.tenant_id),
        "campaign_id": str(b.campaign_id) if b.campaign_id else None,
        "name": b.name,
        "benefit_type": b.benefit_type,
        "config_json": b.config_json,
        "connector_id": str(b.connector_id) if b.connector_id else None,
        "stock_total": b.stock_total,
        "stock_used": b.stock_used,
        "per_person_limit": b.per_person_limit,
        "status": b.status,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }
