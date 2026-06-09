"""会员与积分 API"""

import re
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.member import (
    award_points,
    create_point_rule,
    delete_point_rule,
    get_consumer_profile,
    get_member_overview,
    get_or_create_consumer,
    get_point_rules,
    list_point_redemptions,
    list_point_transactions,
    search_consumers,
    spend_points,
    update_point_rule,
)
from app.services.point_shop import (
    create_point_product,
    delete_point_product,
    exchange_product,
    list_point_products,
    serialize_point_product,
    update_point_product,
)

member_router = APIRouter(prefix="/api/v1/members", tags=["members"])


# ─── Request/Response Schemas ───


class ConsumerCreateRequest(BaseModel):
    phone: str | None = None
    nickname: str | None = Field(None, max_length=100)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^1[3-9]\d{9}$", v):
            raise ValueError("手机号格式不正确")
        return v


class AwardPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)


class SpendPointsRequest(BaseModel):
    consumer_id: uuid.UUID
    points: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=200)
    reference_id: str | None = Field(None, max_length=100)


class PointRuleCreate(BaseModel):
    rule_type: Literal["scan", "first_scan", "register", "checkin", "repurchase", "activity"]
    points: int = Field(gt=0)
    daily_limit: int = Field(ge=0, default=0)
    description: str | None = None
    config: dict | None = None


class PointRuleUpdate(BaseModel):
    points: int | None = None
    daily_limit: int | None = None
    description: str | None = None
    enabled: bool | None = None
    config: dict | None = None


class PointProductCreate(BaseModel):
    name: str = Field(max_length=200)
    description: str | None = None
    image_url: str | None = None
    points_cost: int = Field(gt=0)
    stock: int = Field(ge=0, default=0)
    benefit_id: uuid.UUID | None = None
    enabled: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int = Field(ge=0, default=1)
    sort_order: int = Field(ge=0, default=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class PointProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    points_cost: int | None = None
    stock: int | None = None
    benefit_id: uuid.UUID | None = None
    enabled: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    per_consumer_limit: int | None = None
    sort_order: int | None = None

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class ExchangeRequest(BaseModel):
    consumer_id: uuid.UUID
    product_id: uuid.UUID


# ─── Consumer Endpoints ───


@member_router.get("/overview", summary="会员积分概览")
async def overview_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_member_overview(db, tenant_id)


@member_router.post("/consumers", status_code=201, summary="创建 消费者")
async def create_consumer_endpoint(
    body: ConsumerCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    consumer = await get_or_create_consumer(db, tenant_id, phone=body.phone, nickname=body.nickname)
    return {
        "id": str(consumer.id),
        "nickname": consumer.nickname,
        "member_level": consumer.member_level,
        "total_points": consumer.total_points,
    }


@member_router.get("/consumers/search", summary="搜索消费者")
async def search_consumers_endpoint(
    keyword: str = Query(..., min_length=1),
    lookup_type: str = Query("auto", pattern="^(auto|id|phone|nickname|mixed)$"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return {"items": await search_consumers(db, tenant_id, keyword, lookup_type)}


@member_router.get("/consumers/{consumer_id}", summary="获取 消费者")
async def get_consumer_endpoint(
    consumer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    profile = await get_consumer_profile(db, tenant_id, consumer_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Consumer not found")
    return profile


# ─── Points Award / Spend ───


@member_router.post("/points/award")
async def award_points_endpoint(
    body: AwardPointsRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        txn = await award_points(
            db, tenant_id, body.consumer_id, body.points, body.reason, body.reference_id,
        )
        return {
            "id": str(txn.id),
            "amount": txn.amount,
            "balance_after": txn.balance_after,
            "txn_type": txn.txn_type,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@member_router.post("/points/spend")
async def spend_points_endpoint(
    body: SpendPointsRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        txn = await spend_points(
            db, tenant_id, body.consumer_id, body.points, body.reason, body.reference_id,
        )
        return {
            "id": str(txn.id),
            "amount": txn.amount,
            "balance_after": txn.balance_after,
            "txn_type": txn.txn_type,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@member_router.get("/consumers/{consumer_id}/transactions", summary="transactions 列表")
async def list_transactions_endpoint(
    consumer_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    txns, total = await list_point_transactions(
        db, tenant_id, consumer_id, page=page, page_size=page_size,
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(t.id),
                "amount": t.amount,
                "balance_after": t.balance_after,
                "txn_type": t.txn_type,
                "reason": t.reason,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txns
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── Point Rules CRUD ───


@member_router.get("/point-rules", summary="point rules 列表")
async def list_point_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await get_point_rules(db, tenant_id)
    return [
        {
            "id": str(r.id),
            "rule_type": r.rule_type,
            "points": r.points,
            "daily_limit": r.daily_limit,
            "description": r.description,
            "enabled": r.enabled,
            "config": r.config,
        }
        for r in rules
    ]


@member_router.post("/point-rules", status_code=201, summary="创建 point rule")
async def create_point_rule_endpoint(
    body: PointRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await create_point_rule(
        db, tenant_id, body.rule_type, body.points,
        daily_limit=body.daily_limit,
        description=body.description,
        config=body.config,
    )
    return {
        "id": str(rule.id),
        "rule_type": rule.rule_type,
        "points": rule.points,
        "daily_limit": rule.daily_limit,
    }


@member_router.put("/point-rules/{rule_id}", summary="更新 point rule")
async def update_point_rule_endpoint(
    rule_id: uuid.UUID,
    body: PointRuleUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rule = await update_point_rule(db, tenant_id, rule_id, **body.model_dump(exclude_none=True))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {
        "id": str(rule.id),
        "rule_type": rule.rule_type,
        "points": rule.points,
        "daily_limit": rule.daily_limit,
        "enabled": rule.enabled,
    }


@member_router.delete("/point-rules/{rule_id}", status_code=204, summary="删除 point rule")
async def delete_point_rule_endpoint(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    if not await delete_point_rule(db, tenant_id, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


# ─── Point Products (积分商城) ───


@member_router.get("/point-redemptions", summary="积分兑换记录")
async def list_point_redemptions_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    consumer_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_point_redemptions(
        db,
        tenant_id,
        page=page,
        page_size=page_size,
        consumer_id=consumer_id,
        product_id=product_id,
        status=status,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@member_router.get("/point-products", summary="积分商品列表")
async def list_point_products_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    enabled_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_point_products(db, tenant_id, page, page_size, enabled_only)
    return PaginatedResponse(
        items=[serialize_point_product(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@member_router.post("/point-products", status_code=201, summary="创建积分商品")
async def create_point_product_endpoint(
    body: PointProductCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        product = await create_point_product(
            db, tenant_id,
            name=body.name,
            description=body.description,
            image_url=body.image_url,
            points_cost=body.points_cost,
            stock=body.stock,
            benefit_id=body.benefit_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            per_consumer_limit=body.per_consumer_limit,
            sort_order=body.sort_order,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return serialize_point_product(product)


@member_router.put("/point-products/{product_id}", summary="更新积分商品")
async def update_point_product_endpoint(
    product_id: uuid.UUID,
    body: PointProductUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        product = await update_point_product(
            db, tenant_id, product_id, **body.model_dump(exclude_unset=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_point_product(product)


@member_router.delete("/point-products/{product_id}", status_code=204, summary="删除积分商品")
async def delete_point_product_endpoint(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    if not await delete_point_product(db, tenant_id, product_id):
        raise HTTPException(status_code=404, detail="Product not found")


@member_router.post("/point-products/exchange", summary="积分兑换")
async def exchange_product_endpoint(
    body: ExchangeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        result = await exchange_product(db, tenant_id, body.consumer_id, body.product_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
