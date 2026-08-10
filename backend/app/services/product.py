import uuid
from datetime import date

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.campaign import Campaign
from app.models.code import CodeBatch
from app.models.product import (
    SKU,
    BatchStatus,
    Brand,
    BrandStatus,
    Product,
    ProductAsset,
    ProductAssetStatus,
    ProductAssetType,
    ProductionBatch,
    ProductStatus,
    SKUStatus,
)
from app.schemas.product import ProductionBatchCSVRow
from app.services.quota import CumulativeQuotaKey, check_quota_for_tenant, release_quota
from app.utils import china_business_date, escape_like_pattern
from app.utils.public_url import normalize_public_url


def effective_production_batch_status(
    batch: ProductionBatch,
    *,
    current_date: date | None = None,
) -> BatchStatus:
    """Return the runtime lifecycle without rewriting historical status rows."""
    if batch.status == BatchStatus.active and batch.expiry_date < (current_date or china_business_date()):
        return BatchStatus.expired
    return batch.status


def is_production_batch_effectively_active(
    batch: ProductionBatch,
    *,
    current_date: date | None = None,
) -> bool:
    return effective_production_batch_status(batch, current_date=current_date) == BatchStatus.active


def _validate_active_trust_asset(asset: ProductAsset) -> None:
    if asset.status != ProductAssetStatus.active or asset.asset_type not in {
        ProductAssetType.test_report,
        ProductAssetType.certificate,
    }:
        return
    if not asset.issuer or not asset.issuer.strip():
        raise BadRequestError("Active trust evidence requires an issuer")
    if asset.valid_until is not None and asset.valid_until < date.today():
        raise BadRequestError("Active trust evidence is expired")
    evidence_urls = [value for value in (asset.file_url, asset.image_url) if value]
    if not evidence_urls:
        raise BadRequestError("Active trust evidence requires a public evidence URL")
    for value in evidence_urls:
        try:
            normalize_public_url(value)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc


async def create_brand(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    logo_url: str | None = None,
    description: str | None = None,
) -> Brand:
    existing = await db.execute(select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == name))
    if existing.scalar_one_or_none():
        raise ConflictError("Brand name already exists in this tenant")

    brand = Brand(
        tenant_id=tenant_id,
        name=name,
        logo_url=logo_url,
        description=description,
    )
    db.add(brand)
    await db.flush()
    await db.refresh(brand)
    return brand


async def list_brands(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Brand], int]:
    stmt = select(Brand).where(Brand.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Brand).where(Brand.tenant_id == tenant_id)

    if name:
        escaped = escape_like_pattern(name)
        stmt = stmt.where(Brand.name.ilike(f"%{escaped}%", escape="\\"))
        count_stmt = count_stmt.where(Brand.name.ilike(f"%{escaped}%", escape="\\"))

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(Brand.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    brands = list(result.scalars().all())
    return brands, total


async def update_brand(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
    name: str | None = None,
    logo_url: str | None = None,
    description: str | None = None,
    status: BrandStatus | None = None,
    fields_to_update: set[str] | None = None,
) -> Brand | None:
    fields_to_update = fields_to_update or set()
    result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id).with_for_update())
    brand = result.scalar_one_or_none()
    if not brand:
        return None

    if name is not None:
        # Check uniqueness
        existing = await db.execute(
            select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == name, Brand.id != brand_id)
        )
        if existing.scalar_one_or_none():
            raise ConflictError("Brand name already exists in this tenant")
        brand.name = name
    public_identity_changed = False
    if logo_url is not None or "logo_url" in fields_to_update:
        brand.logo_url = logo_url
        public_identity_changed = True
    if description is not None or "description" in fields_to_update:
        brand.description = description
    if status is not None:
        brand.status = status
    if name is not None:
        public_identity_changed = True

    await db.flush()
    await db.refresh(brand)
    if public_identity_changed:
        product_ids = (
            await db.execute(select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id))
        ).scalars()
        from app.services.resolver_response import invalidate_product_cache

        for product_id in product_ids:
            await invalidate_product_cache(product_id)
    return brand


async def get_brand_with_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
) -> tuple[Brand | None, dict[str, int]]:
    result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id))
    brand = result.scalar_one_or_none()
    if not brand:
        return None, {}

    product_result = await db.execute(
        select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
    )
    product_count = product_result.scalar() or 0

    campaign_result = await db.execute(
        select(func.count())
        .select_from(Campaign)
        .where(
            Campaign.tenant_id == tenant_id,
            Campaign.product_id.in_(
                select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
            ),
        )
    )
    campaign_count = campaign_result.scalar() or 0

    code_batch_result = await db.execute(
        select(func.count())
        .select_from(CodeBatch)
        .where(
            CodeBatch.tenant_id == tenant_id,
            CodeBatch.product_id.in_(
                select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
            ),
        )
    )
    code_batch_count = code_batch_result.scalar() or 0

    batch_result = await db.execute(
        select(func.count())
        .select_from(ProductionBatch)
        .where(
            ProductionBatch.tenant_id == tenant_id,
            ProductionBatch.product_id.in_(
                select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
            ),
        )
    )
    batch_count = batch_result.scalar() or 0

    stats = {
        "product_count": product_count,
        "campaign_count": campaign_count,
        "code_batch_count": code_batch_count,
        "batch_count": batch_count,
    }
    return brand, stats


async def create_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
    name: str,
    category: str | None = None,
    description: str | None = None,
    origin: str | None = None,
    image_url: str | None = None,
    story_title: str | None = None,
    story_content: str | None = None,
) -> Product:
    brand_result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id))
    brand = brand_result.scalar_one_or_none()
    if not brand:
        raise NotFoundError("Brand not found")
    await check_quota_for_tenant(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS, Product)
    product = Product(
        tenant_id=tenant_id,
        brand_id=brand_id,
        name=name,
        category=category,
        origin=origin,
        image_url=image_url,
        story_title=story_title,
        story_content=story_content,
        description=description,
    )
    product.brand = brand
    db.add(product)
    await db.flush()
    await db.refresh(product)
    await db.refresh(product, ["brand"])
    return product


async def list_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID | None = None,
    category: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Product], int]:
    stmt = select(Product).options(selectinload(Product.brand)).where(Product.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)

    if brand_id:
        stmt = stmt.where(Product.brand_id == brand_id)
        count_stmt = count_stmt.where(Product.brand_id == brand_id)
    if category:
        stmt = stmt.where(Product.category == category)
        count_stmt = count_stmt.where(Product.category == category)
    if search:
        escaped = escape_like_pattern(search)
        condition = Product.name.ilike(f"%{escaped}%", escape="\\")
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(Product.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def get_product(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
    result = await db.execute(
        select(Product)
        .options(selectinload(Product.brand))
        .where(Product.id == product_id, Product.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def update_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    brand_id: uuid.UUID | None = None,
    name: str | None = None,
    category: str | None = None,
    description: str | None = None,
    origin: str | None = None,
    image_url: str | None = None,
    story_title: str | None = None,
    story_content: str | None = None,
    status: ProductStatus | None = None,
    fields_to_update: set[str] | None = None,
) -> Product | None:
    fields_to_update = fields_to_update or set()
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id).with_for_update()
    )
    product = result.scalar_one_or_none()
    if not product:
        return None

    if brand_id is not None:
        brand_result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id))
        if not brand_result.scalar_one_or_none():
            raise NotFoundError("Brand not found")
        product.brand_id = brand_id
    if name is not None:
        product.name = name
    if category is not None or "category" in fields_to_update:
        product.category = category
    if origin is not None or "origin" in fields_to_update:
        product.origin = origin
    if image_url is not None or "image_url" in fields_to_update:
        product.image_url = image_url
    if story_title is not None or "story_title" in fields_to_update:
        product.story_title = story_title
    if story_content is not None or "story_content" in fields_to_update:
        product.story_content = story_content
    if description is not None or "description" in fields_to_update:
        product.description = description
    if status is not None:
        product.status = status

    await db.flush()
    await db.refresh(product)
    await db.refresh(product, ["brand"])
    # 失效公共解析缓存：编辑后消费者页立即看到新值（yimatong-zgb1.2 AC1）
    from app.services.resolver_response import invalidate_product_cache

    await invalidate_product_cache(product.id)
    return product


async def create_sku(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    code: str,
    name: str,
    specifications: dict | None = None,
    package_type: str | None = None,
    barcode: str | None = None,
    image_url: str | None = None,
) -> SKU:
    product_result = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    product = product_result.scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found")
    existing = await db.execute(
        select(SKU).where(SKU.tenant_id == tenant_id, SKU.product_id == product_id, SKU.code == code)
    )
    if existing.scalar_one_or_none():
        raise ConflictError("SKU code already exists for this product")

    sku = SKU(
        tenant_id=tenant_id,
        product_id=product_id,
        code=code,
        name=name,
        specifications=specifications,
        package_type=package_type,
        barcode=barcode,
        image_url=image_url,
    )
    sku.product = product
    db.add(sku)
    await db.flush()
    await db.refresh(sku)
    return sku


async def list_skus(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[SKU], int]:
    stmt = select(SKU).options(selectinload(SKU.product)).where(SKU.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(SKU).where(SKU.tenant_id == tenant_id)

    if product_id:
        stmt = stmt.where(SKU.product_id == product_id)
        count_stmt = count_stmt.where(SKU.product_id == product_id)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(SKU.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def update_sku(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    sku_id: uuid.UUID,
    code: str | None = None,
    name: str | None = None,
    specifications: dict | None = None,
    package_type: str | None = None,
    barcode: str | None = None,
    image_url: str | None = None,
    status: SKUStatus | None = None,
    fields_to_update: set[str] | None = None,
) -> SKU | None:
    result = await db.execute(select(SKU).where(SKU.id == sku_id, SKU.tenant_id == tenant_id))
    sku = result.scalar_one_or_none()
    if not sku:
        return None

    if code is not None:
        existing = await db.execute(
            select(SKU).where(
                SKU.tenant_id == tenant_id,
                SKU.product_id == sku.product_id,
                SKU.code == code,
                SKU.id != sku_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError("SKU code already exists for this product")
        sku.code = code
    if name is not None:
        sku.name = name
    if specifications is not None or (fields_to_update and "specifications" in fields_to_update):
        sku.specifications = specifications
    if package_type is not None or (fields_to_update and "package_type" in fields_to_update):
        sku.package_type = package_type
    if barcode is not None or (fields_to_update and "barcode" in fields_to_update):
        sku.barcode = barcode
    if image_url is not None or (fields_to_update and "image_url" in fields_to_update):
        sku.image_url = image_url
    if status is not None:
        sku.status = status

    await db.flush()
    await db.refresh(sku)
    await db.refresh(sku, ["product"])
    return sku


async def get_sku_detail(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    sku_id: uuid.UUID,
) -> SKU | None:
    result = await db.execute(
        select(SKU).options(selectinload(SKU.product)).where(SKU.id == sku_id, SKU.tenant_id == tenant_id)
    )
    sku = result.scalar_one_or_none()
    if not sku:
        return None
    return sku


async def create_production_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    batch_code: str,
    production_date,
    expiry_date,
    origin: str | None = None,
) -> ProductionBatch:
    if expiry_date < production_date:
        raise BadRequestError("Expiry date cannot be earlier than production date")

    product_result = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    product = product_result.scalar_one_or_none()
    sku_result = await db.execute(select(SKU).where(SKU.id == sku_id, SKU.tenant_id == tenant_id))
    sku = sku_result.scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found")
    if not sku:
        raise NotFoundError("SKU not found")
    if sku.product_id != product_id:
        raise BadRequestError("SKU does not belong to selected product")
    existing = await db.execute(
        select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id, ProductionBatch.batch_code == batch_code)
    )
    if existing.scalar_one_or_none():
        raise ConflictError("Batch code already exists in this tenant")

    batch = ProductionBatch(
        tenant_id=tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        batch_code=batch_code,
        production_date=production_date,
        expiry_date=expiry_date,
        origin=origin,
    )
    if product:
        batch.product = product
    if sku:
        batch.sku = sku
    db.add(batch)
    await db.flush()
    await db.refresh(batch)
    await db.refresh(batch, ["product", "sku"])
    return batch


async def update_production_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    batch_code: str | None = None,
    production_date=None,
    expiry_date=None,
    origin: str | None = None,
    fields_to_update: set[str] | None = None,
) -> ProductionBatch | None:
    fields_to_update = fields_to_update or set()
    result = await db.execute(
        select(ProductionBatch)
        .options(selectinload(ProductionBatch.product), selectinload(ProductionBatch.sku))
        .where(ProductionBatch.id == batch_id, ProductionBatch.tenant_id == tenant_id)
        .with_for_update()
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return None
    if not is_production_batch_effectively_active(batch):
        raise ConflictError("Only an active production batch can be updated")

    if batch_code is not None:
        existing = await db.execute(
            select(ProductionBatch).where(
                ProductionBatch.tenant_id == tenant_id,
                ProductionBatch.batch_code == batch_code,
                ProductionBatch.id != batch_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ConflictError("Batch code already exists in this tenant")
        batch.batch_code = batch_code
    if production_date is not None:
        batch.production_date = production_date
    if expiry_date is not None:
        batch.expiry_date = expiry_date
    if "origin" in fields_to_update:
        batch.origin = origin
    if batch.expiry_date < batch.production_date:
        raise BadRequestError("Expiry date cannot be earlier than production date")

    await db.flush()
    await db.refresh(batch)
    # 失效公共解析缓存：生产批次字段被消费者页直接渲染（yimatong-zgb1.2 AC1）。
    # 注意批次变更影响所有引用该批次的码 → 失效其 product 的缓存条目。
    from app.services.resolver_response import invalidate_product_cache

    await invalidate_product_cache(batch.product_id)
    return batch


async def recall_production_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    batch_id: uuid.UUID,
    *,
    reason: str,
    actor_id: uuid.UUID,
) -> ProductionBatch | None:
    from app.services.audit import write_audit_log
    from app.utils import utcnow

    result = await db.execute(
        select(ProductionBatch)
        .options(selectinload(ProductionBatch.product), selectinload(ProductionBatch.sku))
        .where(ProductionBatch.id == batch_id, ProductionBatch.tenant_id == tenant_id)
        .with_for_update()
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        return None
    if not is_production_batch_effectively_active(batch):
        raise ConflictError("Only an active production batch can be recalled")

    batch.status = BatchStatus.recalled
    batch.recall_reason = reason
    batch.recalled_at = utcnow()
    batch.recalled_by = str(actor_id)
    await db.flush()
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "production_batch_recalled",
        f"production_batch:{batch.id}",
        {"resource_name": batch.batch_code, "reason": reason, "result": "success"},
    )

    from app.services.resolver_response import invalidate_product_cache

    await invalidate_product_cache(batch.product_id)
    return batch


async def list_production_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
    sku_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ProductionBatch], int]:
    stmt = (
        select(ProductionBatch)
        .options(selectinload(ProductionBatch.product), selectinload(ProductionBatch.sku))
        .where(ProductionBatch.tenant_id == tenant_id)
    )
    count_stmt = select(func.count()).select_from(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id)

    if product_id:
        stmt = stmt.where(ProductionBatch.product_id == product_id)
        count_stmt = count_stmt.where(ProductionBatch.product_id == product_id)
    if sku_id:
        stmt = stmt.where(ProductionBatch.sku_id == sku_id)
        count_stmt = count_stmt.where(ProductionBatch.sku_id == sku_id)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(ProductionBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def create_product_asset(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    asset_type: ProductAssetType,
    name: str,
    description: str | None = None,
    issuer: str | None = None,
    valid_until=None,
    file_url: str | None = None,
    image_url: str | None = None,
    content_text: str | None = None,
    metadata_json: dict | None = None,
) -> ProductAsset | None:
    product = await get_product(db, tenant_id, product_id)
    if not product:
        return None
    asset = ProductAsset(
        tenant_id=tenant_id,
        product_id=product_id,
        asset_type=asset_type,
        name=name,
        description=description,
        issuer=issuer,
        valid_until=valid_until,
        file_url=file_url,
        image_url=image_url,
        content_text=content_text,
        metadata_json=metadata_json,
    )
    _validate_active_trust_asset(asset)
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    # 失效公共解析缓存：新增资产影响消费者页 test_reports/certificates（yimatong-zgb1.2）
    from app.services.resolver_response import invalidate_product_cache

    await invalidate_product_cache(product_id)
    return asset


async def list_product_assets(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    asset_type: ProductAssetType | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ProductAsset], int]:
    stmt = select(ProductAsset).where(ProductAsset.tenant_id == tenant_id, ProductAsset.product_id == product_id)
    count_stmt = (
        select(func.count())
        .select_from(ProductAsset)
        .where(
            ProductAsset.tenant_id == tenant_id,
            ProductAsset.product_id == product_id,
        )
    )
    if asset_type:
        stmt = stmt.where(ProductAsset.asset_type == asset_type)
        count_stmt = count_stmt.where(ProductAsset.asset_type == asset_type)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0
    stmt = stmt.order_by(ProductAsset.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def update_product_asset(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    asset_type: ProductAssetType | None = None,
    name: str | None = None,
    description: str | None = None,
    issuer: str | None = None,
    valid_until=None,
    file_url: str | None = None,
    image_url: str | None = None,
    content_text: str | None = None,
    metadata_json: dict | None = None,
    status: ProductAssetStatus | None = None,
    fields_to_update: set[str] | None = None,
) -> ProductAsset | None:
    result = await db.execute(
        select(ProductAsset).where(ProductAsset.id == asset_id, ProductAsset.tenant_id == tenant_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        return None

    if asset_type is not None:
        asset.asset_type = asset_type
    if name is not None:
        asset.name = name
    if description is not None or (fields_to_update and "description" in fields_to_update):
        asset.description = description
    if issuer is not None or (fields_to_update and "issuer" in fields_to_update):
        asset.issuer = issuer
    if valid_until is not None or (fields_to_update and "valid_until" in fields_to_update):
        asset.valid_until = valid_until
    if file_url is not None or (fields_to_update and "file_url" in fields_to_update):
        asset.file_url = file_url
    if image_url is not None or (fields_to_update and "image_url" in fields_to_update):
        asset.image_url = image_url
    if content_text is not None or (fields_to_update and "content_text" in fields_to_update):
        asset.content_text = content_text
    if metadata_json is not None or (fields_to_update and "metadata_json" in fields_to_update):
        asset.metadata_json = metadata_json
    if status is not None:
        asset.status = status

    _validate_active_trust_asset(asset)

    await db.flush()
    await db.refresh(asset)
    # 失效公共解析缓存：编辑资产影响消费者页 test_reports/certificates（yimatong-zgb1.2）
    from app.services.resolver_response import invalidate_product_cache

    await invalidate_product_cache(asset.product_id)
    return asset


async def delete_product_asset(db: AsyncSession, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(ProductAsset).where(ProductAsset.id == asset_id, ProductAsset.tenant_id == tenant_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        return False
    product_id = asset.product_id
    await db.delete(asset)
    await db.flush()
    # 失效公共解析缓存：删除资产影响消费者页 test_reports/certificates（yimatong-zgb1.2）
    from app.services.resolver_response import invalidate_product_cache

    await invalidate_product_cache(product_id)
    return True


async def import_batches_csv(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    csv_content: str,
) -> tuple[int, list[str]]:
    import csv
    from io import StringIO

    reader = csv.DictReader(StringIO(csv_content))
    imported = 0
    errors: list[str] = []

    max_rows = 10_000
    max_errors = 100

    product = (
        await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not product:
        raise NotFoundError("Product not found")
    sku = (await db.execute(select(SKU).where(SKU.id == sku_id, SKU.tenant_id == tenant_id))).scalar_one_or_none()
    if not sku:
        raise NotFoundError("SKU not found")
    if sku.product_id != product_id:
        raise BadRequestError("SKU does not belong to selected product")

    for row_num, row in enumerate(reader, start=2):
        if row_num > max_rows + 1:
            if len(errors) < max_errors:
                errors.append(f"Row {row_num}: row limit exceeded ({max_rows})")
            break
        try:
            parsed = ProductionBatchCSVRow.model_validate(row)

            existing = await db.execute(
                select(ProductionBatch).where(
                    ProductionBatch.tenant_id == tenant_id,
                    ProductionBatch.batch_code == parsed.batch_code,
                )
            )
            if existing.scalar_one_or_none():
                if len(errors) < max_errors:
                    errors.append(f"Row {row_num}: batch_code already exists")
                continue

            batch = ProductionBatch(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=sku_id,
                batch_code=parsed.batch_code,
                production_date=parsed.production_date,
                expiry_date=parsed.expiry_date,
                origin=parsed.origin,
            )
            db.add(batch)
            imported += 1
        except ValidationError:
            if len(errors) < max_errors:
                errors.append(f"Row {row_num}: invalid batch data")

    if imported > 0:
        await db.flush()

    return imported, errors


async def check_brand_has_products(db: AsyncSession, tenant_id: uuid.UUID, brand_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
    )
    return (result.scalar() or 0) > 0


async def delete_brand(db: AsyncSession, tenant_id: uuid.UUID, brand_id: uuid.UUID) -> tuple[bool, str | None]:
    """删除品牌。返回 (是否成功, 冲突原因)。"""
    result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id))
    brand = result.scalar_one_or_none()
    if not brand:
        return False, None

    has_products = await check_brand_has_products(db, tenant_id, brand_id)
    if has_products:
        return False, "Brand has associated products"

    await db.delete(brand)
    await db.flush()
    return True, None


async def delete_product(db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> tuple[bool, str | None]:
    """删除产品。返回 (是否成功, 冲突原因)。"""
    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id).with_for_update()
    )
    product = result.scalar_one_or_none()
    if not product:
        return False, None

    sku_count_result = await db.execute(
        select(func.count()).select_from(SKU).where(SKU.tenant_id == tenant_id, SKU.product_id == product_id)
    )
    if (sku_count_result.scalar() or 0) > 0:
        return False, "Product has associated SKUs"

    batch_count_result = await db.execute(
        select(func.count())
        .select_from(ProductionBatch)
        .where(ProductionBatch.tenant_id == tenant_id, ProductionBatch.product_id == product_id)
    )
    if (batch_count_result.scalar() or 0) > 0:
        return False, "Product has associated production batches"

    asset_count_result = await db.execute(
        select(func.count())
        .select_from(ProductAsset)
        .where(ProductAsset.tenant_id == tenant_id, ProductAsset.product_id == product_id)
    )
    if (asset_count_result.scalar() or 0) > 0:
        return False, "Product has associated assets"

    await db.delete(product)
    await release_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
    await db.flush()
    return True, None


async def delete_sku(db: AsyncSession, tenant_id: uuid.UUID, sku_id: uuid.UUID) -> tuple[bool, str | None]:
    """删除 SKU。返回 (是否成功, 冲突原因)。"""
    result = await db.execute(select(SKU).where(SKU.id == sku_id, SKU.tenant_id == tenant_id))
    sku = result.scalar_one_or_none()
    if not sku:
        return False, None

    batch_count_result = await db.execute(
        select(func.count())
        .select_from(ProductionBatch)
        .where(ProductionBatch.tenant_id == tenant_id, ProductionBatch.sku_id == sku_id)
    )
    if (batch_count_result.scalar() or 0) > 0:
        return False, "SKU has associated production batches"

    await db.delete(sku)
    await db.flush()
    return True, None


async def delete_production_batch(
    db: AsyncSession, tenant_id: uuid.UUID, batch_id: uuid.UUID
) -> tuple[bool, str | None]:
    """删除生产批次。返回 (是否成功, 冲突原因)。"""
    result = await db.execute(
        select(ProductionBatch).where(ProductionBatch.id == batch_id, ProductionBatch.tenant_id == tenant_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return False, None

    code_batch_count_result = await db.execute(
        select(func.count())
        .select_from(CodeBatch)
        .where(CodeBatch.tenant_id == tenant_id, CodeBatch.production_batch_id == batch_id)
    )
    if (code_batch_count_result.scalar() or 0) > 0:
        return False, "Production batch has associated code batches"

    await db.delete(batch)
    await db.flush()
    return True, None


async def list_brand_production_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ProductionBatch], int]:
    product_ids_result = await db.execute(
        select(Product.id).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
    )
    product_ids = list(product_ids_result.scalars().all())

    if not product_ids:
        return [], 0

    stmt = (
        select(ProductionBatch)
        .options(selectinload(ProductionBatch.product), selectinload(ProductionBatch.sku))
        .where(ProductionBatch.tenant_id == tenant_id, ProductionBatch.product_id.in_(product_ids))
    )
    count_stmt = (
        select(func.count())
        .select_from(ProductionBatch)
        .where(
            ProductionBatch.tenant_id == tenant_id,
            ProductionBatch.product_id.in_(product_ids),
        )
    )

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(ProductionBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total
