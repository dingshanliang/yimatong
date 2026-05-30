import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import (
    SKU,
    Brand,
    BrandStatus,
    Product,
    ProductionBatch,
    ProductStatus,
    SKUStatus,
)


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
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
) -> Product:
    product = Product(
        tenant_id=tenant_id,
        brand_id=brand_id,
        name=name,
        category=category,
        description=description,
    )
    db.add(product)
    await db.flush()
    await db.refresh(product)
    return product


async def list_products(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Product], int]:
    stmt = select(Product).where(Product.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)

    if brand_id:
        stmt = stmt.where(Product.brand_id == brand_id)
        count_stmt = count_stmt.where(Product.brand_id == brand_id)
    if category:
        stmt = stmt.where(Product.category == category)
        count_stmt = count_stmt.where(Product.category == category)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(Product.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def update_product(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    name: str | None = None,
    category: str | None = None,
    description: str | None = None,
    status: ProductStatus | None = None,
) -> Product | None:
    result = await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    product = result.scalar_one_or_none()
    if not product:
        return None

    if name is not None:
        product.name = name
    if category is not None:
        product.category = category
    if description is not None:
        product.description = description
    if status is not None:
        product.status = status

    await db.flush()
    await db.refresh(product)
    return product


async def create_sku(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    code: str,
    name: str,
    specifications: dict | None = None,
) -> SKU:
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
    )
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
    stmt = select(SKU).where(SKU.tenant_id == tenant_id)
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
    status: SKUStatus | None = None,
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
    if specifications is not None:
        sku.specifications = specifications
    if status is not None:
        sku.status = status

    await db.flush()
    await db.refresh(sku)
    return sku


async def create_production_batch(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    batch_code: str,
    production_date,
    expiry_date,
) -> ProductionBatch:
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
    )
    db.add(batch)
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
    stmt = select(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id)
    count_stmt = select(func.count()).select_from(ProductionBatch).where(ProductionBatch.tenant_id == tenant_id)

    if product_id:
        stmt = stmt.where(ProductionBatch.product_id == product_id)
        count_stmt = count_stmt.where(ProductionBatch.product_id == product_id)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(ProductionBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


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
