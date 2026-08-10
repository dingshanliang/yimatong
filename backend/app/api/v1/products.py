import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.code_batches import CodeBatchRead
from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
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
    ProductionBatchRecallRequest,
    ProductionBatchUpdate,
    ProductRead,
    ProductUpdate,
    SKUCreate,
    SKURead,
    SKUUpdate,
)
from app.services.audit import write_audit_log
from app.services.campaign import list_brand_campaigns
from app.services.code import list_brand_code_batches
from app.services.product import (
    create_brand,
    create_product,
    create_product_asset,
    create_production_batch,
    create_sku,
    delete_brand,
    delete_product,
    delete_product_asset,
    delete_production_batch,
    delete_sku,
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
    recall_production_batch,
    update_brand,
    update_product,
    update_product_asset,
    update_production_batch,
    update_sku,
)
from app.utils.auth_rbac import require_permission, require_role, require_tenant_type

brand_router = APIRouter(prefix="/api/v1/brands", tags=["brands"])
product_router = APIRouter(prefix="/api/v1/products", tags=["products"])
sku_router = APIRouter(prefix="/api/v1/skus", tags=["skus"])
batch_router = APIRouter(prefix="/api/v1/production-batches", tags=["production-batches"])
# NOTE: ProductAsset 创建使用嵌套路由 /products/{id}/assets（明确归属关系），
# 更新/删除使用扁平路由 /product-assets/{id}（直接定位资源），符合 RESTful 最佳实践。
asset_router = APIRouter(prefix="/api/v1/product-assets", tags=["product-assets"])


# --- Brand endpoints ---


@brand_router.post("", response_model=BrandRead, status_code=201, summary="创建 品牌")
async def create_brand_endpoint(
    body: BrandCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    brand = await create_brand(db, tenant_id, body.name, body.logo_url, body.description)
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "brand_created",
        f"brand:{brand.id}",
        {"resource_name": brand.name, "result": "success"},
    )
    return brand


@brand_router.get("", summary="品牌 列表")
async def list_brands_endpoint(
    name: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    brand = await update_brand(
        db,
        tenant_id,
        brand_id,
        name=body.name,
        logo_url=body.logo_url,
        description=body.description,
        status=body.status,
        fields_to_update=body.model_fields_set,
    )
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "brand_updated",
        f"brand:{brand.id}",
        {"resource_name": brand.name, "changed_fields": sorted(body.model_fields_set), "result": "success"},
    )
    return brand


@brand_router.get("/{brand_id}", response_model=BrandDetailRead, summary="品牌 详情")
async def get_brand_endpoint(
    brand_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    deleted, conflict = await delete_brand(db, tenant_id, brand_id)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if not deleted:
        raise HTTPException(status_code=404, detail="Brand not found")
    await write_audit_log(
        db, str(actor_id), str(tenant_id), "brand_deleted", f"brand:{brand_id}", {"result": "success"}
    )


# --- Product endpoints ---


@product_router.post("", response_model=ProductRead, status_code=201, summary="创建 产品")
async def create_product_endpoint(
    body: ProductCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    product = await create_product(
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
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "product_created",
        f"product:{product.id}",
        {"resource_name": product.name, "result": "success"},
    )
    return product


@product_router.get("", summary="产品 列表")
async def list_products_endpoint(
    brand_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
):
    product = await get_product(db, tenant_id, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@product_router.patch("/{product_id}", response_model=ProductRead, summary="更新 产品")
async def update_product_endpoint(
    product_id: uuid.UUID,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
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
        fields_to_update=body.model_fields_set,
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "product_updated",
        f"product:{product.id}",
        {
            "resource_name": product.name,
            "changed_fields": sorted(body.model_dump(exclude_unset=True)),
            "result": "success",
        },
    )
    return product


@product_router.get("/{product_id}/assets", summary="产品资料 列表")
async def list_product_assets_endpoint(
    product_id: uuid.UUID,
    asset_type: ProductAssetType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
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
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "product_asset_created",
        f"product_asset:{asset.id}",
        {"resource_name": asset.name, "product_id": str(product_id), "result": "success"},
    )
    return asset


@product_router.get("/{product_id}/skus")
async def list_product_skus(
    product_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
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


@product_router.delete("/{product_id}", status_code=204, summary="删除 产品")
async def delete_product_endpoint(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    deleted, conflict = await delete_product(db, tenant_id, product_id)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")
    await write_audit_log(
        db, str(actor_id), str(tenant_id), "product_deleted", f"product:{product_id}", {"result": "success"}
    )


# --- SKU endpoints ---


@sku_router.post("", response_model=SKURead, status_code=201, summary="创建 SKU")
async def create_sku_endpoint(
    body: SKUCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    sku = await create_sku(
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
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "sku_created",
        f"sku:{sku.id}",
        {"resource_name": sku.name, "product_id": str(sku.product_id), "result": "success"},
    )
    return sku


@sku_router.get("", summary="SKU 列表")
async def list_skus_endpoint(
    product_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    _role: str = Depends(require_role("admin", "operator")),
):
    sku = await get_sku_detail(db, tenant_id, sku_id)
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    return sku


@sku_router.patch("/{sku_id}", response_model=SKURead, summary="更新 SKU")
async def update_sku_endpoint(
    sku_id: uuid.UUID,
    body: SKUUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
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
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "sku_updated",
        f"sku:{sku.id}",
        {"resource_name": sku.name, "changed_fields": sorted(body.model_fields_set), "result": "success"},
    )
    return sku


@sku_router.delete("/{sku_id}", status_code=204, summary="删除 SKU")
async def delete_sku_endpoint(
    sku_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    deleted, conflict = await delete_sku(db, tenant_id, sku_id)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if not deleted:
        raise HTTPException(status_code=404, detail="SKU not found")
    await write_audit_log(db, str(actor_id), str(tenant_id), "sku_deleted", f"sku:{sku_id}", {"result": "success"})


# --- ProductionBatch endpoints ---


@batch_router.post("", response_model=ProductionBatchRead, status_code=201, summary="创建 批次")
async def create_batch_endpoint(
    body: ProductionBatchCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    batch = await create_production_batch(
        db,
        tenant_id,
        body.product_id,
        body.sku_id,
        body.batch_code,
        body.production_date,
        body.expiry_date,
        origin=body.origin,
    )
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "production_batch_created",
        f"production_batch:{batch.id}",
        {"resource_name": batch.batch_code, "product_id": str(batch.product_id), "result": "success"},
    )
    return batch


@batch_router.get("", summary="批次 列表")
async def list_batches_endpoint(
    product_id: uuid.UUID | None = Query(None),
    sku_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _role: str = Depends(require_role("admin", "operator")),
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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    batch = await update_production_batch(
        db,
        tenant_id,
        batch_id,
        batch_code=body.batch_code,
        production_date=body.production_date,
        expiry_date=body.expiry_date,
        origin=body.origin,
        fields_to_update=body.model_fields_set,
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "production_batch_updated",
        f"production_batch:{batch.id}",
        {"resource_name": batch.batch_code, "changed_fields": sorted(body.model_fields_set), "result": "success"},
    )
    return batch


@batch_router.post("/{batch_id}/recall", response_model=ProductionBatchRead, summary="召回生产批次")
async def recall_batch_endpoint(
    batch_id: uuid.UUID,
    body: ProductionBatchRecallRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _tenant_type: str = Depends(require_tenant_type("brand")),
    _role: str = Depends(require_role("admin")),
    _permission: None = Depends(require_permission("code:manage")),
):
    batch = await recall_production_batch(
        db,
        tenant_id,
        batch_id,
        reason=body.reason,
        actor_id=actor_id,
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@batch_router.post("/import-csv", response_model=CSVImportResult, summary="导入 csv")
async def import_csv_endpoint(
    product_id: str = Form(...),
    sku_id: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
):
    filename = (file.filename or "").lower()
    allowed_types = {"text/csv", "application/csv", "application/vnd.ms-excel"}
    if not filename.endswith(".csv") or file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="Only CSV files are supported")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV file must not exceed 5MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must be UTF-8 encoded") from exc
    imported, errors = await import_batches_csv(
        db,
        tenant_id,
        uuid.UUID(product_id),
        uuid.UUID(sku_id),
        content,
    )
    if imported:
        await write_audit_log(
            db,
            str(actor_id),
            str(tenant_id),
            "production_batch_imported",
            f"product:{product_id}",
            {"product_id": product_id, "sku_id": sku_id, "imported": imported, "errors": len(errors)},
        )
    return CSVImportResult(imported=imported, errors=errors)


@batch_router.delete("/{batch_id}", status_code=204, summary="删除 批次")
async def delete_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    deleted, conflict = await delete_production_batch(db, tenant_id, batch_id)
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if not deleted:
        raise HTTPException(status_code=404, detail="Batch not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "production_batch_deleted",
        f"production_batch:{batch_id}",
        {"result": "success"},
    )


@asset_router.patch("/{asset_id}", response_model=ProductAssetRead, summary="更新 产品资料")
async def update_product_asset_endpoint(
    asset_id: uuid.UUID,
    body: ProductAssetUpdate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
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
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "product_asset_updated",
        f"product_asset:{asset.id}",
        {"resource_name": asset.name, "changed_fields": sorted(body.model_fields_set), "result": "success"},
    )
    return asset


@asset_router.delete("/{asset_id}", status_code=204, summary="删除 产品资料")
async def delete_product_asset_endpoint(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin")),
):
    deleted = await delete_product_asset(db, tenant_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product asset not found")
    await write_audit_log(
        db,
        str(actor_id),
        str(tenant_id),
        "product_asset_deleted",
        f"product_asset:{asset_id}",
        {"result": "success"},
    )
