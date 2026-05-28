"""批量导入端点：产品导入 + 既有码接管"""

import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.code import CodeItem, CodeItemStatus, CodeType
from app.models.product import Brand, Product, SKU
from app.services.public_id import generate_public_id

import_router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@import_router.post("/products")
async def import_products(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    """CSV 批量导入产品"""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    errors = []
    for row in reader:
        try:
            brand_name = row.get("brand_name", "").strip()
            product_name = row.get("product_name", "").strip()
            if not product_name:
                continue

            # 查找或创建品牌
            from sqlalchemy import select
            result = await db.execute(
                select(Brand).where(
                    Brand.tenant_id == tenant_id,
                    Brand.name == brand_name,
                )
            )
            brand = result.scalar_one_or_none()
            if not brand and brand_name:
                brand = Brand(tenant_id=tenant_id, name=brand_name)
                db.add(brand)
                await db.flush()

            product = Product(
                tenant_id=tenant_id,
                brand_id=brand.id if brand else None,
                name=product_name,
                category=row.get("category", ""),
                description=row.get("description", ""),
            )
            db.add(product)
            await db.flush()

            # 创建 SKU
            sku_code = row.get("sku_code", f"SKU-{product.id.hex[:8]}")
            sku = SKU(
                tenant_id=tenant_id,
                product_id=product.id,
                code=sku_code,
                name=row.get("sku_name", "默认规格"),
            )
            db.add(sku)
            imported += 1
        except Exception as e:
            errors.append({"row": row, "error": str(e)})

    await db.commit()
    return {"imported": imported, "errors": errors}


@import_router.post("/existing-codes")
async def import_existing_codes(
    file: UploadFile = File(...),
    code_batch_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    """CSV 导入既有码（接管已有印刷码）"""
    if not code_batch_id:
        raise HTTPException(status_code=400, detail="code_batch_id is required")

    from app.models.code import CodeBatch, CodeBatchStatus
    from sqlalchemy import select

    result = await db.execute(
        select(CodeBatch).where(
            CodeBatch.id == code_batch_id,
            CodeBatch.tenant_id == tenant_id,
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Code batch not found")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    for row in reader:
        public_id = row.get("public_id", "").strip()
        if not public_id:
            continue
        item = CodeItem(
            tenant_id=tenant_id,
            code_batch_id=code_batch_id,
            public_id=public_id,
            status=CodeItemStatus.activated,
            code_type=batch.code_type,
        )
        db.add(item)
        imported += 1

    await db.commit()
    return {"imported": imported, "batch_id": str(code_batch_id)}
