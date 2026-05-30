"""Open API — 外部系统集成接口。

所有端点需要 API Key 认证（X-Api-Key header），受角色权限控制。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.middleware.auth import require_permission
from app.schemas.common import PaginatedResponse

open_api_router = APIRouter(prefix="/open/v1", tags=["open-api"])

# --- Scans ---


@open_api_router.get("/scans")
async def list_scans(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("scan:list")),
):
    from app.models.scan import ScanEvent

    total_result = await db.execute(
        select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(ScanEvent)
        .where(ScanEvent.tenant_id == tenant_id)
        .order_by(ScanEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(s.id),
            "public_id": s.public_id,
            "scan_time": s.scan_time.isoformat() if s.scan_time else None,
            "is_first_scan": s.is_first_scan,
            "environment": s.environment,
        }
        for s in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@open_api_router.get("/scans/{scan_id}")
async def get_scan(
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("scan:detail")),
):
    from app.models.scan import ScanEvent

    result = await db.execute(
        select(ScanEvent).where(ScanEvent.id == scan_id, ScanEvent.tenant_id == tenant_id)
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Scan not found")
    return {
        "id": str(s.id),
        "public_id": s.public_id,
        "scan_time": s.scan_time.isoformat() if s.scan_time else None,
        "ip_hash": s.ip_hash,
        "user_agent": s.user_agent,
        "is_first_scan": s.is_first_scan,
        "environment": s.environment,
    }


# --- Consumers ---


@open_api_router.get("/consumers")
async def list_consumers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("consumer:list")),
):
    from app.models.member import ConsumerProfile

    total_result = await db.execute(
        select(func.count()).select_from(ConsumerProfile).where(ConsumerProfile.tenant_id == tenant_id)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(ConsumerProfile)
        .where(ConsumerProfile.tenant_id == tenant_id)
        .order_by(ConsumerProfile.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(c.id),
            "member_level": c.member_level,
            "total_points": c.total_points,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@open_api_router.get("/consumers/{consumer_id}")
async def get_consumer(
    consumer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("consumer:detail")),
):
    from app.services.member import get_consumer_profile

    profile = await get_consumer_profile(db, tenant_id, consumer_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Consumer not found")
    return profile


# --- Claims ---


@open_api_router.get("/claims")
async def list_claims(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("claim:list")),
):
    from app.models.campaign import BenefitClaim

    total_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(BenefitClaim.tenant_id == tenant_id)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(BenefitClaim)
        .where(BenefitClaim.tenant_id == tenant_id)
        .order_by(BenefitClaim.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(c.id),
            "benefit_id": str(c.benefit_id),
            "consumer_id": str(c.consumer_id),
            "campaign_id": str(c.campaign_id),
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


# --- Events (scan event stats) ---


@open_api_router.get("/events")
async def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("event:list")),
):
    from app.models.scan import ScanEvent

    total_result = await db.execute(
        select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(ScanEvent)
        .where(ScanEvent.tenant_id == tenant_id)
        .order_by(ScanEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(e.id),
            "public_id": e.public_id,
            "scan_time": e.scan_time.isoformat() if e.scan_time else None,
            "environment": e.environment,
        }
        for e in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


# --- Coupon Operations (coupon_operator) ---


class CouponIssueRequest(BaseModel):
    consumer_id: str
    benefit_id: str
    idempotency_key: str


@open_api_router.post("/coupons/issue")
async def issue_coupon(
    body: CouponIssueRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("coupon:issue")),
):
    from app.services.campaign import claim_benefit

    result = await claim_benefit(
        db,
        tenant_id,
        benefit_id=uuid.UUID(body.benefit_id),
        consumer_id=body.consumer_id,
        idempotency_key=body.idempotency_key,
    )
    return result


class CouponRedeemRequest(BaseModel):
    claim_id: str


@open_api_router.post("/coupons/{coupon_id}/redeem")
async def redeem_coupon(
    coupon_id: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("coupon:redeem")),
):
    # 标记为已使用
    from app.models.campaign import BenefitClaim

    result = await db.execute(
        select(BenefitClaim).where(
            BenefitClaim.id == uuid.UUID(coupon_id),
            BenefitClaim.tenant_id == tenant_id,
        )
    )
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Coupon claim not found")
    return {"id": str(claim.id), "status": "redeemed"}


# --- Full Access Operations ---


class CampaignStatusRequest(BaseModel):
    status: str


@open_api_router.patch("/campaigns/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: uuid.UUID,
    body: CampaignStatusRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:status")),
):
    from app.services.campaign import change_campaign_status

    result = await change_campaign_status(db, tenant_id, campaign_id, body.status)
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result
