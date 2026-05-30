import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.product import (
    BrandCreate,
    BrandRead,
    BrandUpdate,
    CSVImportResult,
    PaginatedResponse,
    ProductCreate,
    ProductionBatchCreate,
    ProductionBatchRead,
    ProductRead,
    ProductUpdate,
    SKUCreate,
    SKURead,
    SKUUpdate,
)
from app.services.product import (
    check_brand_has_products,
    create_brand,
    create_product,
    create_production_batch,
    create_sku,
    import_batches_csv,
    list_brands,
    list_production_batches,
    list_products,
    list_skus,
    update_brand,
    update_product,
    update_sku,
)

brand_router = APIRouter(prefix="/api/v1/brands", tags=["brands"])
product_router = APIRouter(prefix="/api/v1/products", tags=["products"])
sku_router = APIRouter(prefix="/api/v1/skus", tags=["skus"])
batch_router = APIRouter(prefix="/api/v1/production-batches", tags=["production-batches"])


# --- Brand endpoints ---


@brand_router.post("", response_model=BrandRead, status_code=201)
async def create_brand_endpoint(
    body: BrandCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_brand(db, tenant_id, body.name, body.logo_url, body.description)


@brand_router.get("")
async def list_brands_endpoint(
    name: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    brands, total = await list_brands(db, tenant_id, name=name, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[BrandRead.model_validate(b) for b in brands],
        total=total,
        page=page,
        page_size=page_size,
    )


@brand_router.patch("/{brand_id}", response_model=BrandRead)
async def update_brand_endpoint(
    brand_id: uuid.UUID,
    body: BrandUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    brand = await update_brand(
        db,
        tenant_id,
        brand_id,
        name=body.name,
        logo_url=body.logo_url,
        description=body.description,
        status=body.status,
    )
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    return brand


@brand_router.get("/{brand_id}/products")
async def list_brand_products(
    brand_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    products, total = await list_products(
        db,
        tenant_id,
        brand_id=brand_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductRead.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )


@brand_router.delete("/{brand_id}", status_code=204)
async def delete_brand_endpoint(
    brand_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    has_products = await check_brand_has_products(db, tenant_id, brand_id)
    if has_products:
        raise HTTPException(status_code=409, detail="Cannot delete brand with existing products")


# --- Product endpoints ---


@product_router.post("", response_model=ProductRead, status_code=201)
async def create_product_endpoint(
    body: ProductCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_product(db, tenant_id, body.brand_id, body.name, body.category, body.description)


@product_router.get("")
async def list_products_endpoint(
    brand_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    products, total = await list_products(
        db,
        tenant_id,
        brand_id=brand_id,
        category=category,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductRead.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )


@product_router.patch("/{product_id}", response_model=ProductRead)
async def update_product_endpoint(
    product_id: uuid.UUID,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    product = await update_product(
        db,
        tenant_id,
        product_id,
        name=body.name,
        category=body.category,
        description=body.description,
        status=body.status,
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@product_router.get("/{product_id}/skus")
async def list_product_skus(
    product_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    skus, total = await list_skus(
        db,
        tenant_id,
        product_id=product_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[SKURead.model_validate(s) for s in skus],
        total=total,
        page=page,
        page_size=page_size,
    )


@product_router.get("/{product_id}/batches")
async def list_product_batches(
    product_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_production_batches(
        db,
        tenant_id,
        product_id=product_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductionBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


# --- SKU endpoints ---


@sku_router.post("", response_model=SKURead, status_code=201)
async def create_sku_endpoint(
    body: SKUCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_sku(
        db,
        tenant_id,
        body.product_id,
        body.code,
        body.name,
        body.specifications,
    )


@sku_router.get("")
async def list_skus_endpoint(
    product_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    skus, total = await list_skus(
        db,
        tenant_id,
        product_id=product_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[SKURead.model_validate(s) for s in skus],
        total=total,
        page=page,
        page_size=page_size,
    )


@sku_router.patch("/{sku_id}", response_model=SKURead)
async def update_sku_endpoint(
    sku_id: uuid.UUID,
    body: SKUUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    sku = await update_sku(
        db,
        tenant_id,
        sku_id,
        code=body.code,
        name=body.name,
        specifications=body.specifications,
        status=body.status,
    )
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


# --- ProductionBatch endpoints ---


@batch_router.post("", response_model=ProductionBatchRead, status_code=201)
async def create_batch_endpoint(
    body: ProductionBatchCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_production_batch(
        db,
        tenant_id,
        body.product_id,
        body.sku_id,
        body.batch_code,
        body.production_date,
        body.expiry_date,
    )


@batch_router.get("")
async def list_batches_endpoint(
    product_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_production_batches(
        db,
        tenant_id,
        product_id=product_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductionBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


@batch_router.post("/import-csv", response_model=CSVImportResult)
async def import_csv_endpoint(
    product_id: str = Form(...),
    sku_id: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    content = (await file.read()).decode("utf-8")
    imported, errors = await import_batches_csv(
        db,
        tenant_id,
        uuid.UUID(product_id),
        uuid.UUID(sku_id),
        content,
    )
    return CSVImportResult(imported=imported, errors=errors)
