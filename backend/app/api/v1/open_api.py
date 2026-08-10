"""Open API — 外部系统集成接口。

所有端点需要 API Key 认证（X-Api-Key header），受角色权限控制。
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.core.event_bus import event_bus
from app.schemas.campaign import CampaignStatusRequest
from app.schemas.common import PaginatedResponse
from app.services.audit import write_audit_log
from app.services.quota import CumulativeQuotaKey, reserve_quota
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("coupon:redeem")),
):
    """核销一张券：把 BenefitClaim 标记为已使用，记审计并发 claim.used 事件。

    之前这里是空操作（只返回 status=redeemed，不改任何状态），外部系统会误以为
    已核销。改为真正写入 used 状态；重复核销同一张券返回 409。
    """
    from app.models.campaign import BenefitClaim

    claim = (
        await db.execute(
            select(BenefitClaim)
            .where(BenefitClaim.id == uuid.UUID(coupon_id), BenefitClaim.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Coupon claim not found")
    if claim.status == "used":
        raise HTTPException(status_code=409, detail="Coupon already redeemed")

    claim.status = "used"
    # Open API 使用 API Key 鉴权，account_id 为 None；用 api_key_id 作为操作人追溯。
    operator = getattr(request.state, "api_key_id", None) or "open_api"
    await write_audit_log(
        db,
        operator_id=str(operator),
        target_tenant_id=str(tenant_id),
        action="coupon_redeemed",
        resource=f"claim:{claim.id}",
    )
    await db.flush()
    await event_bus.emit(
        "claim.used",
        {"claim_id": str(claim.id), "benefit_id": str(claim.benefit_id), "consumer_id": claim.consumer_id},
        str(tenant_id),
    )
    await db.commit()
    return {"id": str(claim.id), "status": "used"}


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


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _normalize_open_specifications(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > 50:
        raise ValueError("Specifications may contain at most 50 entries")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key, item = raw_key.strip(), raw_value.strip()
        if not key or not item or len(key) > 100 or len(item) > 500:
            raise ValueError("Specification keys and values must be nonblank and bounded")
        normalized[key] = item
    return normalized


class ProductCreateRequest(CatalogRequest):
    name: str = Field(min_length=1, max_length=200)
    brand_id: uuid.UUID | None = None
    brand_name: str | None = Field(None, min_length=1, max_length=100)
    category: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=1000)
    external_id: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def require_one_brand_reference(self):
        if (self.brand_id is None) == (self.brand_name is None):
            raise ValueError("Exactly one of brand_id or brand_name is required")
        return self


class ProductUpdateRequest(CatalogRequest):
    name: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=1000)


class SkuCreateRequest(CatalogRequest):
    product_id: uuid.UUID | None = None
    product_name: str | None = Field(None, min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    specifications: dict[str, str] | None = None
    external_id: str | None = Field(None, max_length=100)

    _normalize_specifications = field_validator("specifications")(_normalize_open_specifications)

    @model_validator(mode="after")
    def require_one_product_reference(self):
        if (self.product_id is None) == (self.product_name is None):
            raise ValueError("Exactly one of product_id or product_name is required")
        return self


class SkuUpdateRequest(CatalogRequest):
    name: str | None = Field(None, min_length=1, max_length=200)
    specifications: dict[str, str] | None = None

    _normalize_specifications = field_validator("specifications")(_normalize_open_specifications)


class BatchCreateRequest(CatalogRequest):
    sku_id: uuid.UUID | None = None
    sku_code: str | None = Field(None, min_length=1, max_length=100)
    batch_code: str = Field(min_length=1, max_length=100)
    production_date: date
    expiry_date: date
    external_id: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def validate_batch(self):
        if (self.sku_id is None) == (self.sku_code is None):
            raise ValueError("Exactly one of sku_id or sku_code is required")
        if self.expiry_date < self.production_date:
            raise ValueError("expiry_date cannot be earlier than production_date")
        return self


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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:create")),
):
    from app.models.product import Brand, Product

    brand_query = select(Brand).where(Brand.tenant_id == tenant_id)
    if body.brand_id is not None:
        brand_query = brand_query.where(Brand.id == body.brand_id)
    else:
        brand_query = brand_query.where(Brand.name == body.brand_name)
    brand = (await db.execute(brand_query)).scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail="品牌不存在")

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
        if "category" in body.model_fields_set:
            existing.category = body.category
        if "description" in body.model_fields_set:
            existing.description = body.description
        await db.flush()
        await write_audit_log(
            db,
            "open_api",
            str(tenant_id),
            "product_updated",
            f"product:{existing.id}",
            {"resource_name": existing.name, "source": "open_api", "result": "success"},
        )
        return {
            "id": str(existing.id),
            "name": existing.name,
            "external_id": existing.external_id,
            "action": "updated",
        }

    product = Product(
        tenant_id=tenant_id,
        brand_id=brand.id,
        name=body.name,
        category=body.category,
        description=body.description,
        external_id=body.external_id,
        source_system="open_api" if body.external_id else None,
    )
    await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
    db.add(product)
    await db.flush()
    await write_audit_log(
        db,
        "open_api",
        str(tenant_id),
        "product_created",
        f"product:{product.id}",
        {"resource_name": product.name, "source": "open_api", "result": "success"},
    )
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    if "category" in body.model_fields_set:
        product.category = body.category
    if "description" in body.model_fields_set:
        product.description = body.description
    await db.flush()
    await write_audit_log(
        db,
        "open_api",
        str(tenant_id),
        "product_updated",
        f"product:{product.id}",
        {"resource_name": product.name, "source": "open_api", "result": "success"},
    )
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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:create")),
):
    from app.models.product import SKU, Product

    product_query = select(Product).where(Product.tenant_id == tenant_id)
    if body.product_id is not None:
        product_query = product_query.where(Product.id == body.product_id)
    else:
        product_query = product_query.where(Product.name == body.product_name).limit(2)
    products = list((await db.execute(product_query)).scalars().all())
    if not products:
        raise HTTPException(status_code=404, detail="产品不存在")
    if len(products) > 1:
        raise HTTPException(status_code=409, detail="产品名称不唯一，请改用 product_id")
    product = products[0]

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
        if "specifications" in body.model_fields_set:
            existing.specifications = body.specifications
        await db.flush()
        await write_audit_log(
            db,
            "open_api",
            str(tenant_id),
            "sku_updated",
            f"sku:{existing.id}",
            {"resource_name": existing.name, "source": "open_api", "result": "success"},
        )
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
    await db.flush()
    await write_audit_log(
        db,
        "open_api",
        str(tenant_id),
        "sku_created",
        f"sku:{sku.id}",
        {"resource_name": sku.name, "product_id": str(sku.product_id), "source": "open_api", "result": "success"},
    )
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    if "specifications" in body.model_fields_set:
        sku.specifications = body.specifications
    await db.flush()
    await write_audit_log(
        db,
        "open_api",
        str(tenant_id),
        "sku_updated",
        f"sku:{sku.id}",
        {"resource_name": sku.name, "source": "open_api", "result": "success"},
    )
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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_permission("product:create")),
):
    from app.models.product import SKU, ProductionBatch

    sku_query = select(SKU).where(SKU.tenant_id == tenant_id)
    if body.sku_id is not None:
        sku_query = sku_query.where(SKU.id == body.sku_id)
    else:
        sku_query = sku_query.where(SKU.code == body.sku_code).limit(2)
    skus = list((await db.execute(sku_query)).scalars().all())
    if not skus:
        raise HTTPException(status_code=404, detail="SKU 不存在")
    if len(skus) > 1:
        raise HTTPException(status_code=409, detail="SKU 编码不唯一，请改用 sku_id")
    sku = skus[0]

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
        existing.production_date = body.production_date
        existing.expiry_date = body.expiry_date
        await db.flush()
        await write_audit_log(
            db,
            "open_api",
            str(tenant_id),
            "production_batch_updated",
            f"production_batch:{existing.id}",
            {"resource_name": existing.batch_code, "source": "open_api", "result": "success"},
        )
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
        production_date=body.production_date,
        expiry_date=body.expiry_date,
        external_id=body.external_id,
        source_system="open_api" if body.external_id else None,
    )
    db.add(batch)
    await db.flush()
    await write_audit_log(
        db,
        "open_api",
        str(tenant_id),
        "production_batch_created",
        f"production_batch:{batch.id}",
        {"resource_name": batch.batch_code, "sku_id": str(batch.sku_id), "source": "open_api", "result": "success"},
    )
    return {
        "id": str(batch.id),
        "batch_code": batch.batch_code,
        "external_id": batch.external_id,
        "action": "created",
    }
