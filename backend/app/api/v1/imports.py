"""批量导入端点：产品导入 + 既有码接管 + Excel 多 Sheet 导入"""

import csv
import io
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.code import CodeItem, CodeItemStatus
from app.models.product import SKU, Brand, Product
from app.services.import_service import ExcelImportService

logger = logging.getLogger(__name__)

import_router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

# 最大文件大小：10 MB
MAX_EXCEL_SIZE = 10 * 1024 * 1024


@import_router.get("/template")
async def download_import_template():
    """下载 Excel 多 Sheet 导入模板（品牌/产品/SKU/批次）"""
    service = ExcelImportService()
    buf = service.generate_template()
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=import_template.xlsx",
        },
    )


@import_router.post("/excel")
async def import_excel(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    """上传 Excel 文件进行多 Sheet 导入（品牌 → 产品 → SKU → 批次）"""
    # 校验文件类型
    filename = file.filename or ""
    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 或 .xls 格式的 Excel 文件")

    content = await file.read()
    if len(content) > MAX_EXCEL_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

    service = ExcelImportService()

    # 解析与校验
    parsed = await service.parse_and_validate(content, tenant_id)
    if parsed.errors:
        # 存在文件级解析错误时直接返回
        parse_errors = [{"sheet": e.sheet, "row": e.row, "message": e.message} for e in parsed.errors]
        return {
            "success": False,
            "message": "文件解析失败",
            "errors": parse_errors,
            "created": 0,
            "updated": 0,
        }

    if not parsed.has_data:
        return {
            "success": False,
            "message": "文件中无有效数据，请检查 Sheet 名称是否为：品牌、产品、SKU、批次",
            "created": 0,
            "updated": 0,
        }

    # 执行导入
    report = await service.execute_import(parsed, tenant_id, db, account_id)
    await db.commit()

    # 构建错误信息
    all_errors = []
    for err in report.brands.errors:
        all_errors.append({"sheet": err.sheet, "row": err.row, "message": err.message})
    for err in report.products.errors:
        all_errors.append({"sheet": err.sheet, "row": err.row, "message": err.message})
    for err in report.skus.errors:
        all_errors.append({"sheet": err.sheet, "row": err.row, "message": err.message})
    for err in report.batches.errors:
        all_errors.append({"sheet": err.sheet, "row": err.row, "message": err.message})

    return {
        "success": report.total_errors == 0,
        "message": "导入完成" if report.total_errors == 0 else f"导入完成，有 {report.total_errors} 个错误",
        "created": report.total_created,
        "updated": report.total_updated,
        "details": {
            "brands": {
                "total": report.brands.total,
                "created": report.brands.created,
                "updated": report.brands.updated,
                "errors": len(report.brands.errors),
            },
            "products": {
                "total": report.products.total,
                "created": report.products.created,
                "updated": report.products.updated,
                "errors": len(report.products.errors),
            },
            "skus": {
                "total": report.skus.total,
                "created": report.skus.created,
                "updated": report.skus.updated,
                "errors": len(report.skus.errors),
            },
            "batches": {
                "total": report.batches.total,
                "created": report.batches.created,
                "updated": report.batches.updated,
                "errors": len(report.batches.errors),
            },
        },
        "errors": all_errors,
    }


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

    from sqlalchemy import select

    from app.models.code import CodeBatch

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
