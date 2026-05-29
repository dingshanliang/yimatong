"""活动与权益 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.services.campaign import (
    change_campaign_status,
    claim_benefit,
    create_benefit,
    create_campaign,
    delete_campaign,
    get_campaign,
    list_benefits,
    list_campaigns,
    update_campaign,
)
from app.services.campaign_analytics import get_campaign_comparison, get_campaign_funnel

campaign_router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


REQUIRED_RULES_FIELDS = [
    "participation_conditions",
    "claim_limits",
    "validity_period",
    "disclaimer",
    "minor_notice",
    "customer_service_contact",
]


class CampaignCreateRequest(BaseModel):
    name: str
    campaign_type: str
    start_at: str
    end_at: str
    rules_json: dict
    description: str | None = None

    @field_validator("rules_json")
    @classmethod
    def validate_rules_json(cls, v: dict) -> dict:
        missing = [f for f in REQUIRED_RULES_FIELDS if f not in v]
        if missing:
            raise ValueError(f"rules_json 缺少必填字段: {', '.join(missing)}")
        return v


class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    description: str | None = None


class CampaignStatusRequest(BaseModel):
    status: str


class BenefitCreateRequest(BaseModel):
    name: str
    benefit_type: str
    config_json: dict
    stock_total: int
    per_person_limit: int = 1


class ClaimRequest(BaseModel):
    consumer_id: str
    idempotency_key: str


@campaign_router.post("", status_code=201)
async def create_campaign_endpoint(
    body: CampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_campaign(
        db, tenant_id, body.name, body.campaign_type,
        body.start_at, body.end_at, body.rules_json, body.description,
    )


@campaign_router.get("")
async def list_campaigns_endpoint(
    status: str | None = Query(None),
    campaign_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_campaigns(
        db, tenant_id, status=status, campaign_type=campaign_type,
        page=page, page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@campaign_router.get("/{campaign_id}")
async def get_campaign_endpoint(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await get_campaign(db, tenant_id, campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.patch("/{campaign_id}")
async def update_campaign_endpoint(
    campaign_id: uuid.UUID,
    body: CampaignUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await update_campaign(
        db, tenant_id, campaign_id,
        **body.model_dump(exclude_none=True),
    )
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.post("/{campaign_id}/status")
async def change_campaign_status_endpoint(
    campaign_id: uuid.UUID,
    body: CampaignStatusRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await change_campaign_status(db, tenant_id, campaign_id, body.status)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.delete("/{campaign_id}")
async def delete_campaign_endpoint(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deleted = await delete_campaign(db, tenant_id, campaign_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Only draft campaigns can be deleted")
    return {"status": "deleted"}


# --- Benefits ---

@campaign_router.post("/{campaign_id}/benefits", status_code=201)
async def create_benefit_endpoint(
    campaign_id: uuid.UUID,
    body: BenefitCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_benefit(
        db, tenant_id, campaign_id, body.name, body.benefit_type,
        body.config_json, body.stock_total, body.per_person_limit,
    )


@campaign_router.get("/{campaign_id}/benefits")
async def list_benefits_endpoint(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await list_benefits(db, tenant_id, campaign_id)


# --- Claim ---

@campaign_router.post("/benefits/{benefit_id}/claim")
async def claim_benefit_endpoint(
    benefit_id: uuid.UUID,
    body: ClaimRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await claim_benefit(
        db, tenant_id, benefit_id, body.consumer_id, body.idempotency_key,
    )
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Benefit not found")
    if result["status"] == "out_of_stock":
        raise HTTPException(status_code=410, detail="权益已抢光")
    if result["status"] == "limit_reached":
        raise HTTPException(status_code=403, detail="您已达到本次活动领取上限")
    return result


# --- Analytics ---

@campaign_router.get("/analytics/funnel")
async def campaign_funnel_endpoint(
    campaign_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await get_campaign_funnel(db, tenant_id, campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.get("/analytics/comparison")
async def campaign_comparison_endpoint(
    campaign_ids: str = Query(..., description="逗号分隔的活动ID"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    ids = [uuid.UUID(x.strip()) for x in campaign_ids.split(",")[:10]]
    return await get_campaign_comparison(db, tenant_id, ids)
