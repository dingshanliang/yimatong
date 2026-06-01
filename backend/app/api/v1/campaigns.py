"""活动与权益 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.campaign import (
    attach_benefit_to_campaign,
    campaign_product_exists,
    change_campaign_status,
    claim_benefit,
    create_benefit,
    create_campaign,
    delete_campaign,
    get_campaign,
    get_campaign_activation_blockers,
    list_benefits,
    list_campaigns,
    update_campaign,
    validate_benefit_config_shape,
    validate_campaign_rules_shape,
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


def _request_product_id(product_id: uuid.UUID | None, rules_json: dict | None) -> uuid.UUID | None:
    if product_id is not None:
        return product_id
    raw = (rules_json or {}).get("product_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


class CampaignCreateRequest(BaseModel):
    name: str
    campaign_type: str
    product_id: uuid.UUID | None = None
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
        return validate_campaign_rules_shape(v)


class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    campaign_type: str | None = None
    product_id: uuid.UUID | None = None
    start_at: str | None = None
    end_at: str | None = None
    rules_json: dict | None = None
    description: str | None = None

    @field_validator("rules_json")
    @classmethod
    def validate_rules_json(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        return validate_campaign_rules_shape(v)


class CampaignStatusRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"draft", "active", "paused", "ended"}
        if v not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return v


class BenefitCreateRequest(BaseModel):
    name: str
    benefit_type: str
    config_json: dict
    stock_total: int
    per_person_limit: int = 1
    connector_id: uuid.UUID | None = None

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, v: dict) -> dict:
        return validate_benefit_config_shape(v)


class ClaimRequest(BaseModel):
    consumer_id: str
    idempotency_key: str


@campaign_router.post("", status_code=201, summary="创建活动")
async def create_campaign_endpoint(
    body: CampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    product_id = _request_product_id(body.product_id, body.rules_json)
    if product_id and not await campaign_product_exists(db, tenant_id, product_id):
        raise HTTPException(status_code=400, detail="Product not found")
    return await create_campaign(
        db,
        tenant_id,
        body.name,
        body.campaign_type,
        body.start_at,
        body.end_at,
        body.rules_json,
        body.description,
        product_id,
    )


@campaign_router.get("", summary="活动列表")
async def list_campaigns_endpoint(
    status: str | None = Query(None),
    campaign_type: str | None = Query(None),
    product_id: uuid.UUID | None = Query(None),
    computed_status: str | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_campaigns(
        db,
        tenant_id,
        status=status,
        campaign_type=campaign_type,
        product_id=product_id,
        computed_status=computed_status,
        q=q,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@campaign_router.get("/{campaign_id}", summary="获取活动详情")
async def get_campaign_endpoint(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await get_campaign(db, tenant_id, campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.patch("/{campaign_id}", summary="更新活动")
async def update_campaign_endpoint(
    campaign_id: uuid.UUID,
    body: CampaignUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    product_id = _request_product_id(body.product_id, body.rules_json)
    if product_id is not None and not await campaign_product_exists(db, tenant_id, product_id):
        raise HTTPException(status_code=400, detail="Product not found")
    data = await update_campaign(
        db,
        tenant_id,
        campaign_id,
        **body.model_dump(exclude_unset=True),
    )
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.post("/{campaign_id}/status", summary="修改活动状态")
async def change_campaign_status_endpoint(
    campaign_id: uuid.UUID,
    body: CampaignStatusRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    if body.status == "active":
        blockers = await get_campaign_activation_blockers(db, tenant_id, campaign_id)
        if blockers is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if blockers:
            raise HTTPException(status_code=400, detail="；".join(blockers))
    data = await change_campaign_status(db, tenant_id, campaign_id, body.status)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.delete("/{campaign_id}", summary="删除活动")
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


@campaign_router.post("/{campaign_id}/benefits", status_code=201, summary="创建权益")
async def create_benefit_endpoint(
    campaign_id: uuid.UUID,
    body: BenefitCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_benefit(
        db,
        tenant_id,
        campaign_id,
        body.name,
        body.benefit_type,
        body.config_json,
        body.stock_total,
        body.per_person_limit,
        connector_id=body.connector_id,
    )


@campaign_router.post("/{campaign_id}/benefits/{benefit_id}/attach", summary="活动使用权益")
async def attach_benefit_endpoint(
    campaign_id: uuid.UUID,
    benefit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        data = await attach_benefit_to_campaign(db, tenant_id, campaign_id, benefit_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not data:
        raise HTTPException(status_code=404, detail="Campaign or benefit not found")
    return data


@campaign_router.get("/{campaign_id}/benefits", summary="权益列表")
async def list_benefits_endpoint(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await list_benefits(db, tenant_id, campaign_id)


# --- Claim ---


@campaign_router.post("/benefits/{benefit_id}/claim", summary="领取权益")
async def claim_benefit_endpoint(
    benefit_id: uuid.UUID,
    body: ClaimRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    result = await claim_benefit(
        db,
        tenant_id,
        benefit_id,
        body.consumer_id,
        body.idempotency_key,
    )
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Benefit not found")
    if result["status"] == "out_of_stock":
        raise HTTPException(status_code=410, detail="权益已抢光")
    if result["status"] == "limit_reached":
        raise HTTPException(status_code=403, detail="您已达到本次活动领取上限")
    return result


# --- Analytics ---


@campaign_router.get("/analytics/funnel", summary="活动漏斗分析", response_description="漏斗数据")
async def campaign_funnel_endpoint(
    campaign_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    data = await get_campaign_funnel(db, tenant_id, campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return data


@campaign_router.get("/analytics/comparison", summary="活动对比分析", response_description="对比数据")
async def campaign_comparison_endpoint(
    campaign_ids: str = Query(..., description="逗号分隔的活动ID"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    ids = [uuid.UUID(x.strip()) for x in campaign_ids.split(",")[:10]]
    return await get_campaign_comparison(db, tenant_id, ids)
