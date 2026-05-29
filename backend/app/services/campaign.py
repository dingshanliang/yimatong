"""活动与权益服务层"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignStatus


async def create_campaign(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    campaign_type: str,
    start_at: str,
    end_at: str,
    rules_json: dict,
    description: str | None = None,
) -> dict:
    c = Campaign(
        tenant_id=tenant_id,
        name=name,
        campaign_type=campaign_type,
        start_at=start_at,
        end_at=end_at,
        rules_json=rules_json,
        description=description,
    )
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return _campaign_to_dict(c)


async def list_campaigns(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: str | None = None,
    campaign_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt = select(Campaign).where(Campaign.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Campaign).where(
        Campaign.tenant_id == tenant_id,
    )
    if status:
        stmt = stmt.where(Campaign.status == status)
        count_stmt = count_stmt.where(Campaign.status == status)
    if campaign_type:
        stmt = stmt.where(Campaign.campaign_type == campaign_type)
        count_stmt = count_stmt.where(Campaign.campaign_type == campaign_type)

    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(Campaign.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return [_campaign_to_dict(c) for c in result.scalars().all()], total


async def get_campaign(
    db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID,
) -> dict | None:
    result = await db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id),
    )
    c = result.scalar_one_or_none()
    return _campaign_to_dict(c) if c else None


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
        if v is not None:
            setattr(c, k, v)
    await db.flush()
    await db.refresh(c)
    return _campaign_to_dict(c)


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
    return _campaign_to_dict(c)


async def delete_campaign(
    db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID,
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
) -> dict:
    b = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        name=name,
        benefit_type=benefit_type,
        config_json=config_json,
        stock_total=stock_total,
        per_person_limit=per_person_limit,
    )
    db.add(b)
    await db.flush()
    await db.refresh(b)
    return _benefit_to_dict(b)


async def list_benefits(
    db: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID,
) -> list[dict]:
    result = await db.execute(
        select(Benefit).where(
            Benefit.tenant_id == tenant_id, Benefit.campaign_id == campaign_id,
        ).order_by(Benefit.id.desc())
    )
    return [_benefit_to_dict(b) for b in result.scalars().all()]


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
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.benefit_id == benefit_id,
            BenefitClaim.consumer_id == consumer_id,
        )
    )
    claimed_count = count_result.scalar() or 0
    if claimed_count >= benefit.per_person_limit:
        return {"status": "limit_reached"}

    # 扣减库存
    await db.execute(
        update(Benefit)
        .where(Benefit.id == benefit_id)
        .values(stock_used=Benefit.stock_used + 1)
    )

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

    return {"status": "success", "claim": _claim_to_dict(claim)}


def _campaign_to_dict(c: Campaign) -> dict:
    return {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "name": c.name,
        "campaign_type": c.campaign_type,
        "status": c.status,
        "start_at": c.start_at,
        "end_at": c.end_at,
        "rules_json": c.rules_json,
        "description": c.description,
    }


def _benefit_to_dict(b: Benefit) -> dict:
    return {
        "id": str(b.id),
        "tenant_id": str(b.tenant_id),
        "campaign_id": str(b.campaign_id),
        "name": b.name,
        "benefit_type": b.benefit_type,
        "config_json": b.config_json,
        "stock_total": b.stock_total,
        "stock_used": b.stock_used,
        "per_person_limit": b.per_person_limit,
        "status": b.status,
    }


def _claim_to_dict(c: BenefitClaim) -> dict:
    return {
        "id": str(c.id),
        "tenant_id": str(c.tenant_id),
        "benefit_id": str(c.benefit_id),
        "campaign_id": str(c.campaign_id),
        "consumer_id": c.consumer_id,
        "claim_type": c.claim_type,
        "status": c.status,
    }
