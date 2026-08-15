"""会员与积分 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_role, get_current_tenant
from app.schemas.common import PaginatedResponse
from app.schemas.member import (
    AwardPointsRequest,
    ConsumerCreateRequest,
    ExchangeRequest,
    PointProductCreate,
    PointProductUpdate,
    PointRuleCreate,
    PointRuleUpdate,
    SpendPointsRequest,
)
from app.services.member import (
    award_points,
    create_anonymous_consumer_profile_authority,
    create_point_rule,
    delete_point_rule,
    get_consumer_profile,
    get_member_overview,
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
from app.utils.auth_rbac import require_permission


def require_admin_or_operator(role: str = Depends(get_current_role)) -> str:
    if role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return role


member_router = APIRouter(prefix="/api/v1/members", tags=["members"])


# ─── Consumer Endpoints ───


@member_router.get(
    "/overview",
    summary="会员积分概览",
    dependencies=[Depends(require_permission("consumer:detail"))],
)
async def overview_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_member_overview(db, tenant_id)


@member_router.post("/consumers", status_code=201, summary="创建 消费者")
async def create_consumer_endpoint(
    body: ConsumerCreateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_admin_or_operator),
    _permission: None = Depends(require_permission("consumer:detail")),
):
    if body.phone is not None or body.nickname is not None:
        raise HTTPException(status_code=403, detail="consumer_consent_required")
    consumer = await create_anonymous_consumer_profile_authority(db, tenant_id)
    return {
        "id": str(consumer["consumer_id"]),
        "nickname": None,
        "member_level": "normal",
        "total_points": 0,
    }


@member_router.get(
    "/consumers/search",
    summary="搜索消费者",
    dependencies=[Depends(require_permission("consumer:detail"))],
)
async def search_consumers_endpoint(
    keyword: str = Query(..., min_length=1),
    lookup_type: str = Query("auto", pattern="^(auto|id|phone|nickname|mixed)$"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items = await search_consumers(db, tenant_id, keyword, lookup_type, limit=limit)
    return PaginatedResponse(items=items, total=len(items), page=1, page_size=limit)


@member_router.get(
    "/consumers/{consumer_id}",
    summary="获取 消费者",
    dependencies=[Depends(require_permission("consumer:detail"))],
)
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
    _role: str = Depends(require_admin_or_operator),
):
    try:
        txn = await award_points(
            db,
            tenant_id,
            body.consumer_id,
            body.points,
            body.reason,
            body.reference_id,
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
    _role: str = Depends(require_admin_or_operator),
):
    try:
        txn = await spend_points(
            db,
            tenant_id,
            body.consumer_id,
            body.points,
            body.reason,
            body.reference_id,
        )
        return {
            "id": str(txn.id),
            "amount": txn.amount,
            "balance_after": txn.balance_after,
            "txn_type": txn.txn_type,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@member_router.get(
    "/consumers/{consumer_id}/transactions",
    summary="transactions 列表",
    dependencies=[Depends(require_permission("consumer:detail"))],
)
async def list_transactions_endpoint(
    consumer_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    txns, total = await list_point_transactions(
        db,
        tenant_id,
        consumer_id,
        page=page,
        page_size=page_size,
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


@member_router.get("/point-rules", summary="积分规则列表")
async def list_point_rules_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    rules = await get_point_rules(db, tenant_id)
    items = [
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
    return PaginatedResponse(items=items, total=len(items), page=page, page_size=page_size)


@member_router.post("/point-rules", status_code=201, summary="创建 point rule")
async def create_point_rule_endpoint(
    body: PointRuleCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_admin_or_operator),
):
    rule = await create_point_rule(
        db,
        tenant_id,
        body.rule_type,
        body.points,
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
    _role: str = Depends(require_admin_or_operator),
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
    _role: str = Depends(require_admin_or_operator),
):
    if not await delete_point_rule(db, tenant_id, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


# ─── Point Products (积分商城) ───


@member_router.get(
    "/point-redemptions",
    summary="积分兑换记录",
    dependencies=[Depends(require_permission("consumer:detail"))],
)
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
    _role: str = Depends(require_admin_or_operator),
):
    try:
        product = await create_point_product(
            db,
            tenant_id,
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
    _role: str = Depends(require_admin_or_operator),
):
    try:
        product = await update_point_product(db, tenant_id, product_id, **body.model_dump(exclude_unset=True))
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
    _role: str = Depends(require_admin_or_operator),
):
    if not await delete_point_product(db, tenant_id, product_id):
        raise HTTPException(status_code=404, detail="Product not found")


@member_router.post("/point-products/exchange", summary="积分兑换")
async def exchange_product_endpoint(
    body: ExchangeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_admin_or_operator),
):
    try:
        result = await exchange_product(db, tenant_id, body.consumer_id, body.product_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
