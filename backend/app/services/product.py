import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product import (
    SKU,
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
from app.utils import escape_like_pattern


async def create_brand(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    logo_url: str | None = None,
    description: str | None = None,
) -> Brand:
    existing = await db.execute(select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == name))
    if existing.scalar_one_or_none():
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Brand name already exists in this tenant")

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
) -> Brand | None:
    result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id))
    brand = result.scalar_one_or_none()
    if not brand:
        return None

    if name is not None:
        # Check uniqueness
        existing = await db.execute(
            select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == name, Brand.id != brand_id)
        )
        if existing.scalar_one_or_none():
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail="Brand name already exists in this tenant")
        brand.name = name
    if logo_url is not None:
        brand.logo_url = logo_url
    if description is not None:
        brand.description = description
    if status is not None:
        brand.status = status

    await db.flush()
    await db.refresh(brand)
    return brand


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
    if brand:
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
) -> Product | None:
    result = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    product = result.scalar_one_or_none()
    if not product:
        return None

    if brand_id is not None:
        brand_result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.tenant_id == tenant_id))
        if not brand_result.scalar_one_or_none():
            raise ValueError("Brand not found")
        product.brand_id = brand_id
    if name is not None:
        product.name = name
    if category is not None:
        product.category = category
    if origin is not None:
        product.origin = origin
    if image_url is not None:
        product.image_url = image_url
    if story_title is not None:
        product.story_title = story_title
    if story_content is not None:
        product.story_content = story_content
    if description is not None:
        product.description = description
    if status is not None:
        product.status = status

    await db.flush()
    await db.refresh(product)
    await db.refresh(product, ["brand"])
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
    existing = await db.execute(select(SKU).where(SKU.product_id == product_id, SKU.code == code))
    if existing.scalar_one_or_none():
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="SKU code already exists for this product")

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
    if product:
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
                SKU.product_id == sku.product_id,
                SKU.code == code,
                SKU.id != sku_id,
            )
        )
        if existing.scalar_one_or_none():
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail="SKU code already exists for this product")
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
        raise ValueError("Expiry date cannot be earlier than production date")

    product_result = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    product = product_result.scalar_one_or_none()
    sku_result = await db.execute(select(SKU).where(SKU.id == sku_id, SKU.tenant_id == tenant_id))
    sku = sku_result.scalar_one_or_none()
    if not product:
        raise ValueError("Product not found")
    if not sku:
        raise ValueError("SKU not found")
    if sku.product_id != product_id:
        raise ValueError("SKU does not belong to selected product")
    existing = await db.execute(
        select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id, ProductionBatch.batch_code == batch_code)
    )
    if existing.scalar_one_or_none():
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Batch code already exists in this tenant")

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
    status=None,
    fields_to_update: set[str] | None = None,
) -> ProductionBatch | None:
    fields_to_update = fields_to_update or set()
    result = await db.execute(
        select(ProductionBatch)
        .options(selectinload(ProductionBatch.product), selectinload(ProductionBatch.sku))
        .where(ProductionBatch.id == batch_id, ProductionBatch.tenant_id == tenant_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return None

    if batch_code is not None:
        existing = await db.execute(
            select(ProductionBatch).where(
                ProductionBatch.tenant_id == tenant_id,
                ProductionBatch.batch_code == batch_code,
                ProductionBatch.id != batch_id,
            )
        )
        if existing.scalar_one_or_none():
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail="Batch code already exists in this tenant")
        batch.batch_code = batch_code
    if production_date is not None:
        batch.production_date = production_date
    if expiry_date is not None:
        batch.expiry_date = expiry_date
    if "origin" in fields_to_update:
        batch.origin = origin
    if status is not None:
        batch.status = status
    if batch.expiry_date < batch.production_date:
        raise ValueError("Expiry date cannot be earlier than production date")

    await db.flush()
    await db.refresh(batch)
    return batch


async def list_production_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID | None = None,
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
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
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
    count_stmt = select(func.count()).select_from(ProductAsset).where(
        ProductAsset.tenant_id == tenant_id,
        ProductAsset.product_id == product_id,
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

    await db.flush()
    await db.refresh(asset)
    return asset


async def delete_product_asset(db: AsyncSession, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(ProductAsset).where(ProductAsset.id == asset_id, ProductAsset.tenant_id == tenant_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        return False
    await db.delete(asset)
    await db.flush()
    return True


async def import_batches_csv(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    csv_content: str,
) -> tuple[int, list[str]]:
    import csv
    from datetime import date as date_type
    from io import StringIO

    reader = csv.DictReader(StringIO(csv_content))
    imported = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):
        try:
            batch_code = row["batch_code"].strip()
            production_date_str = row["production_date"].strip()
            expiry_date_str = row["expiry_date"].strip()
            production_date = date_type.fromisoformat(production_date_str)
            expiry_date = date_type.fromisoformat(expiry_date_str)

            existing = await db.execute(
                select(ProductionBatch).where(
                    ProductionBatch.tenant_id == tenant_id,
                    ProductionBatch.batch_code == batch_code,
                )
            )
            if existing.scalar_one_or_none():
                errors.append(f"Row {row_num}: batch_code '{batch_code}' already exists")
                continue

            batch = ProductionBatch(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=sku_id,
                batch_code=batch_code,
                production_date=production_date,
                expiry_date=expiry_date,
                origin=row.get("origin", "").strip() or None,
            )
            db.add(batch)
            imported += 1
        except Exception as e:
            errors.append(f"Row {row_num}: {e}")

    if imported > 0:
        await db.flush()

    return imported, errors


async def check_brand_has_products(db: AsyncSession, tenant_id: uuid.UUID, brand_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id, Product.brand_id == brand_id)
    )
    return (result.scalar() or 0) > 0
