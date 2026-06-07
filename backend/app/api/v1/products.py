import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.code_batches import CodeBatchRead
from app.core.database import get_db
from app.core.dependencies import get_current_tenant
from app.schemas.product import (
    BrandCreate,
    BrandDetailRead,
    BrandRead,
    BrandStats,
    BrandUpdate,
    CSVImportResult,
    PaginatedResponse,
    ProductAssetCreate,
    ProductAssetRead,
    ProductAssetType,
    ProductAssetUpdate,
    ProductCreate,
    ProductionBatchCreate,
    ProductionBatchRead,
    ProductionBatchUpdate,
    ProductRead,
    ProductUpdate,
    SKUCreate,
    SKURead,
    SKUUpdate,
)
from app.services.campaign import list_brand_campaigns
from app.services.code import list_brand_code_batches
from app.models.product import Product
from app.models.tenant import Tenant
from app.services.quota import QuotaExceededError, check_quota_incremental
from app.services.product import (
    check_brand_has_products,
    create_brand,
    create_product,
    create_product_asset,
    create_production_batch,
    create_sku,
    delete_product_asset,
    get_brand_with_stats,
    get_product,
    get_sku_detail,
    import_batches_csv,
    list_brand_production_batches,
    list_brands,
    list_product_assets,
    list_production_batches,
    list_products,
    list_skus,
    update_brand,
    update_product,
    update_product_asset,
    update_production_batch,
    update_sku,
)

brand_router = APIRouter(prefix="/api/v1/brands", tags=["brands"])
product_router = APIRouter(prefix="/api/v1/products", tags=["products"])
sku_router = APIRouter(prefix="/api/v1/skus", tags=["skus"])
batch_router = APIRouter(prefix="/api/v1/production-batches", tags=["production-batches"])
asset_router = APIRouter(prefix="/api/v1/product-assets", tags=["product-assets"])


# --- Brand endpoints ---


@brand_router.post("", response_model=BrandRead, status_code=201, summary="创建 品牌")
async def create_brand_endpoint(
    body: BrandCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await create_brand(db, tenant_id, body.name, body.logo_url, body.description)


@brand_router.get("", summary="品牌 列表")
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


@brand_router.patch("/{brand_id}", response_model=BrandRead, summary="更新 品牌")
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


@brand_router.get("/{brand_id}", response_model=BrandDetailRead, summary="品牌 详情")
async def get_brand_endpoint(
    brand_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    brand, stats = await get_brand_with_stats(db, tenant_id, brand_id)
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    return BrandDetailRead(
        **BrandRead.model_validate(brand).model_dump(),
        stats=BrandStats(**stats),
    )


@brand_router.get("/{brand_id}/products", summary="品牌下 产品 列表")
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


@brand_router.get("/{brand_id}/campaigns", summary="品牌下 营销活动 列表")
async def list_brand_campaigns_endpoint(
    brand_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_brand_campaigns(db, tenant_id, brand_id, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@brand_router.get("/{brand_id}/code-batches", summary="品牌下 溯源码批次 列表")
async def list_brand_code_batches_endpoint(
    brand_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_brand_code_batches(db, tenant_id, brand_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[CodeBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


@brand_router.get("/{brand_id}/production-batches", summary="品牌下 生产批次 列表")
async def list_brand_production_batches_endpoint(
    brand_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_brand_production_batches(db, tenant_id, brand_id, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[ProductionBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


@brand_router.delete("/{brand_id}", status_code=204, summary="删除 品牌")
async def delete_brand_endpoint(
    brand_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    has_products = await check_brand_has_products(db, tenant_id, brand_id)
    if has_products:
        raise HTTPException(status_code=409, detail="Cannot delete brand with existing products")


# --- Product endpoints ---


@product_router.post("", response_model=ProductRead, status_code=201, summary="创建 产品")
async def create_product_endpoint(
    body: ProductCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    # Quota check
    tenant = await db.get(Tenant, tenant_id)
    if tenant and tenant.quota:
        total_products = (
            await db.execute(
                select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)
            )
        ).scalar() or 0
        try:
            check_quota_incremental(tenant.quota, "max_products", total_products, 1)
        except QuotaExceededError as e:
            raise HTTPException(status_code=429, detail=str(e))

    return await create_product(
        db,
        tenant_id,
        body.brand_id,
        body.name,
        body.category,
        body.description,
        origin=body.origin,
        image_url=body.image_url,
        story_title=body.story_title,
        story_content=body.story_content,
    )


@product_router.get("", summary="产品 列表")
async def list_products_endpoint(
    brand_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
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
        search=search,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductRead.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )


@product_router.get("/{product_id}", response_model=ProductRead, summary="产品 详情")
async def get_product_endpoint(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    product = await get_product(db, tenant_id, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@product_router.patch("/{product_id}", response_model=ProductRead, summary="更新 产品")
async def update_product_endpoint(
    product_id: uuid.UUID,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        product = await update_product(
            db,
            tenant_id,
            product_id,
            brand_id=body.brand_id,
            name=body.name,
            category=body.category,
            origin=body.origin,
            image_url=body.image_url,
            story_title=body.story_title,
            story_content=body.story_content,
            description=body.description,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@product_router.get("/{product_id}/assets", summary="产品资料 列表")
async def list_product_assets_endpoint(
    product_id: uuid.UUID,
    asset_type: ProductAssetType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    if not await get_product(db, tenant_id, product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    assets, total = await list_product_assets(
        db,
        tenant_id,
        product_id,
        asset_type=asset_type,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductAssetRead.model_validate(a) for a in assets],
        total=total,
        page=page,
        page_size=page_size,
    )


@product_router.post("/{product_id}/assets", response_model=ProductAssetRead, status_code=201, summary="创建 产品资料")
async def create_product_asset_endpoint(
    product_id: uuid.UUID,
    body: ProductAssetCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    asset = await create_product_asset(
        db,
        tenant_id,
        product_id,
        body.asset_type,
        body.name,
        description=body.description,
        issuer=body.issuer,
        valid_until=body.valid_until,
        file_url=body.file_url,
        image_url=body.image_url,
        content_text=body.content_text,
        metadata_json=body.metadata_json,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Product not found")
    return asset


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


@sku_router.post("", response_model=SKURead, status_code=201, summary="创建 SKU")
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
        package_type=body.package_type,
        barcode=body.barcode,
        image_url=body.image_url,
    )


@sku_router.get("", summary="SKU 列表")
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


@sku_router.get("/{sku_id}", response_model=SKURead, summary="SKU 详情")
async def get_sku_endpoint(
    sku_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    sku = await get_sku_detail(db, tenant_id, sku_id)
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


@sku_router.patch("/{sku_id}", response_model=SKURead, summary="更新 SKU")
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
        package_type=body.package_type,
        barcode=body.barcode,
        image_url=body.image_url,
        status=body.status,
        fields_to_update=body.model_fields_set,
    )
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


# --- ProductionBatch endpoints ---


@batch_router.post("", response_model=ProductionBatchRead, status_code=201, summary="创建 批次")
async def create_batch_endpoint(
    body: ProductionBatchCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        return await create_production_batch(
            db,
            tenant_id,
            body.product_id,
            body.sku_id,
            body.batch_code,
            body.production_date,
            body.expiry_date,
            origin=body.origin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@batch_router.get("", summary="批次 列表")
async def list_batches_endpoint(
    product_id: uuid.UUID | None = Query(None),
    sku_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    batches, total = await list_production_batches(
        db,
        tenant_id,
        product_id=product_id,
        sku_id=sku_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[ProductionBatchRead.model_validate(b) for b in batches],
        total=total,
        page=page,
        page_size=page_size,
    )


@batch_router.patch("/{batch_id}", response_model=ProductionBatchRead, summary="更新 批次")
async def update_batch_endpoint(
    batch_id: uuid.UUID,
    body: ProductionBatchUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    try:
        batch = await update_production_batch(
            db,
            tenant_id,
            batch_id,
            batch_code=body.batch_code,
            production_date=body.production_date,
            expiry_date=body.expiry_date,
            origin=body.origin,
            status=getattr(body, "status", None),
            fields_to_update=body.model_fields_set,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@batch_router.post("/import-csv", response_model=CSVImportResult, summary="导入 csv")
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


@asset_router.patch("/{asset_id}", response_model=ProductAssetRead, summary="更新 产品资料")
async def update_product_asset_endpoint(
    asset_id: uuid.UUID,
    body: ProductAssetUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    asset = await update_product_asset(
        db,
        tenant_id,
        asset_id,
        asset_type=body.asset_type,
        name=body.name,
        description=body.description,
        issuer=body.issuer,
        valid_until=body.valid_until,
        file_url=body.file_url,
        image_url=body.image_url,
        content_text=body.content_text,
        metadata_json=body.metadata_json,
        status=body.status,
        fields_to_update=body.model_fields_set,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Product asset not found")
    return asset


@asset_router.delete("/{asset_id}", status_code=204, summary="删除 产品资料")
async def delete_product_asset_endpoint(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    deleted = await delete_product_asset(db, tenant_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product asset not found")
