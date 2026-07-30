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
from app.schemas.campaign import CampaignStatusRequest
from app.schemas.common import PaginatedResponse
from app.utils.auth_rbac import require_permission

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

    total_result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id))
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

    result = await db.execute(select(ScanEvent).where(ScanEvent.id == scan_id, ScanEvent.tenant_id == tenant_id))
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

    total_result = await db.execute(select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id))
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


@open_api_router.patch("/campaigns/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: uuid.UUID,
    body: CampaignStatusRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("campaign:status")),
):
    from app.services.campaign import change_campaign_status, get_campaign_activation_blockers

    if body.status == "active":
        blockers = await get_campaign_activation_blockers(db, tenant_id, campaign_id)
        if blockers is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if blockers:
            raise HTTPException(status_code=400, detail="；".join(blockers))

    result = await change_campaign_status(db, tenant_id, campaign_id, body.status)
    if not result:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return result


# --- Products / SKU / Batch CRUD (ERP integration) ---


class ProductCreateRequest(BaseModel):
    name: str
    brand_name: str | None = None
    category: str | None = None
    description: str | None = None
    external_id: str | None = None


class ProductUpdateRequest(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None


class SkuCreateRequest(BaseModel):
    product_name: str
    code: str
    name: str
    specifications: dict | None = None
    external_id: str | None = None


class SkuUpdateRequest(BaseModel):
    name: str | None = None
    specifications: dict | None = None


class BatchCreateRequest(BaseModel):
    sku_code: str
    batch_code: str
    production_date: str
    expiry_date: str
    external_id: str | None = None


@open_api_router.get("/products")
async def open_list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:list")),
):
    from app.models.product import Product

    total_result = await db.execute(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id))
    total = total_result.scalar() or 0

    result = await db.execute(
        select(Product)
        .where(Product.tenant_id == tenant_id)
        .order_by(Product.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(p.id),
            "name": p.name,
            "category": p.category,
            "description": p.description,
            "external_id": p.external_id,
            "source_system": p.source_system,
            "status": p.status,
        }
        for p in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@open_api_router.post("/products", status_code=201)
async def open_create_product(
    body: ProductCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:create")),
):
    from app.models.product import Brand, Product

    brand_id = None
    if body.brand_name:
        bresult = await db.execute(select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == body.brand_name))
        brand = bresult.scalar_one_or_none()
        if not brand:
            raise HTTPException(status_code=400, detail=f"品牌 '{body.brand_name}' 不存在")
        brand_id = brand.id

    existing = None
    if body.external_id:
        eresult = await db.execute(
            select(Product).where(
                Product.tenant_id == tenant_id,
                Product.source_system == "open_api",
                Product.external_id == body.external_id,
            )
        )
        existing = eresult.scalar_one_or_none()

    if existing:
        if body.name is not None:
            existing.name = body.name
        if body.category is not None:
            existing.category = body.category
        if body.description is not None:
            existing.description = body.description
        await db.commit()
        return {
            "id": str(existing.id),
            "name": existing.name,
            "external_id": existing.external_id,
            "action": "updated",
        }

    product = Product(
        tenant_id=tenant_id,
        brand_id=brand_id,
        name=body.name,
        category=body.category,
        description=body.description,
        external_id=body.external_id,
        source_system="open_api" if body.external_id else None,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return {
        "id": str(product.id),
        "name": product.name,
        "external_id": product.external_id,
        "action": "created",
    }


@open_api_router.patch("/products/{product_id}")
async def open_update_product(
    product_id: uuid.UUID,
    body: ProductUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:update")),
):
    from app.models.product import Product

    result = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")

    if body.name is not None:
        product.name = body.name
    if body.category is not None:
        product.category = body.category
    if body.description is not None:
        product.description = body.description
    await db.commit()
    return {"id": str(product.id), "name": product.name}


@open_api_router.get("/skus")
async def open_list_skus(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:list")),
):
    from app.models.product import SKU

    total_result = await db.execute(select(func.count()).select_from(SKU).where(SKU.tenant_id == tenant_id))
    total = total_result.scalar() or 0

    result = await db.execute(
        select(SKU)
        .where(SKU.tenant_id == tenant_id)
        .order_by(SKU.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(s.id),
            "product_id": str(s.product_id),
            "code": s.code,
            "name": s.name,
            "specifications": s.specifications,
            "external_id": s.external_id,
            "source_system": s.source_system,
        }
        for s in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@open_api_router.post("/skus", status_code=201)
async def open_create_sku(
    body: SkuCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:create")),
):
    from app.models.product import SKU, Product

    presult = await db.execute(select(Product).where(Product.tenant_id == tenant_id, Product.name == body.product_name))
    product = presult.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=400, detail=f"产品 '{body.product_name}' 不存在")

    existing = None
    if body.external_id:
        eresult = await db.execute(
            select(SKU).where(
                SKU.tenant_id == tenant_id,
                SKU.source_system == "open_api",
                SKU.external_id == body.external_id,
            )
        )
        existing = eresult.scalar_one_or_none()

    if existing:
        if body.name is not None:
            existing.name = body.name
        if body.specifications is not None:
            existing.specifications = body.specifications
        await db.commit()
        return {
            "id": str(existing.id),
            "code": existing.code,
            "external_id": existing.external_id,
            "action": "updated",
        }

    sku = SKU(
        tenant_id=tenant_id,
        product_id=product.id,
        code=body.code,
        name=body.name,
        specifications=body.specifications,
        external_id=body.external_id,
        source_system="open_api" if body.external_id else None,
    )
    db.add(sku)
    await db.commit()
    await db.refresh(sku)
    return {
        "id": str(sku.id),
        "code": sku.code,
        "external_id": sku.external_id,
        "action": "created",
    }


@open_api_router.patch("/skus/{sku_id}")
async def open_update_sku(
    sku_id: uuid.UUID,
    body: SkuUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:update")),
):
    from app.models.product import SKU

    result = await db.execute(select(SKU).where(SKU.id == sku_id, SKU.tenant_id == tenant_id))
    sku = result.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=404, detail="SKU 不存在")

    if body.name is not None:
        sku.name = body.name
    if body.specifications is not None:
        sku.specifications = body.specifications
    await db.commit()
    return {"id": str(sku.id), "code": sku.code}


@open_api_router.get("/batches")
async def open_list_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:list")),
):
    from app.models.product import ProductionBatch

    total_result = await db.execute(
        select(func.count()).select_from(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(ProductionBatch)
        .where(ProductionBatch.tenant_id == tenant_id)
        .order_by(ProductionBatch.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        {
            "id": str(b.id),
            "product_id": str(b.product_id),
            "sku_id": str(b.sku_id),
            "batch_code": b.batch_code,
            "production_date": str(b.production_date),
            "expiry_date": str(b.expiry_date),
            "external_id": b.external_id,
            "source_system": b.source_system,
        }
        for b in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@open_api_router.post("/batches", status_code=201)
async def open_create_batch(
    body: BatchCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:create")),
):
    from datetime import date as date_type

    from app.models.product import SKU, ProductionBatch

    sresult = await db.execute(select(SKU).where(SKU.tenant_id == tenant_id, SKU.code == body.sku_code))
    sku = sresult.scalar_one_or_none()
    if not sku:
        raise HTTPException(status_code=400, detail=f"SKU 编码 '{body.sku_code}' 不存在")

    existing = None
    if body.external_id:
        eresult = await db.execute(
            select(ProductionBatch).where(
                ProductionBatch.tenant_id == tenant_id,
                ProductionBatch.source_system == "open_api",
                ProductionBatch.external_id == body.external_id,
            )
        )
        existing = eresult.scalar_one_or_none()

    if existing:
        existing.batch_code = body.batch_code
        existing.production_date = date_type.fromisoformat(body.production_date)
        existing.expiry_date = date_type.fromisoformat(body.expiry_date)
        await db.commit()
        return {
            "id": str(existing.id),
            "batch_code": existing.batch_code,
            "external_id": existing.external_id,
            "action": "updated",
        }

    batch = ProductionBatch(
        tenant_id=tenant_id,
        product_id=sku.product_id,
        sku_id=sku.id,
        batch_code=body.batch_code,
        production_date=date_type.fromisoformat(body.production_date),
        expiry_date=date_type.fromisoformat(body.expiry_date),
        external_id=body.external_id,
        source_system="open_api" if body.external_id else None,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return {
        "id": str(batch.id),
        "batch_code": batch.batch_code,
        "external_id": batch.external_id,
        "action": "created",
    }
