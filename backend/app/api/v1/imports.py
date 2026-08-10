"""批量导入端点：产品导入 + 既有码接管 + Excel 多 Sheet 导入"""

import csv
import hashlib
import hmac
import io
import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.core.exceptions import AppException
from app.models.code import CodeBatch, CodeBatchStatus, CodeItem, CodeItemStatus
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.services.audit import write_audit_log
from app.services.import_service import ExcelImportService, XLSXParseLease
from app.services.product import is_production_batch_effectively_active
from app.services.public_id import validate_public_id
from app.services.quota import CumulativeQuotaKey, reserve_quota
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.auth_rbac import require_permission, require_role

logger = logging.getLogger(__name__)

import_router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

# 最大文件大小：10 MB
MAX_EXCEL_SIZE = 10 * 1024 * 1024
MAX_CSV_SIZE = 5 * 1024 * 1024
MAX_CSV_ROWS = 10_000
MAX_IMPORT_ERRORS = 100
IMPORT_RATE_LIMIT_MAX_ATTEMPTS = 10
IMPORT_RATE_LIMIT_WINDOW_SECONDS = 60

_import_rate_cache = AsyncRedisCache(prefix="import_security", default_ttl=IMPORT_RATE_LIMIT_WINDOW_SECONDS)


async def _enforce_import_rate_limit(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
) -> None:
    secret = (settings.hmac_pepper or settings.secret_key).encode()
    digest = hmac.new(secret, f"{tenant_id}:{account_id}".encode(), hashlib.sha256).hexdigest()
    try:
        allowed, _ = await _import_rate_cache.rate_limit_check_shared(
            f"principal:{digest}",
            IMPORT_RATE_LIMIT_MAX_ATTEMPTS,
            IMPORT_RATE_LIMIT_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="Import service is temporarily unavailable") from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many import requests",
            headers={"Retry-After": str(IMPORT_RATE_LIMIT_WINDOW_SECONDS)},
        )


async def _reserve_excel_parse_capacity(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _rate_limit: None = Depends(_enforce_import_rate_limit),
) -> AsyncGenerator[XLSXParseLease, None]:
    service = ExcelImportService()
    lease = service.reserve_parse_capacity(tenant_id)
    if lease is None:
        raise HTTPException(status_code=503, detail="Excel import capacity is temporarily full")
    try:
        yield lease
    finally:
        lease.release()


async def _read_upload(file: UploadFile, *, max_size: int, suffixes: tuple[str, ...], content_types: set[str]) -> bytes:
    filename = (file.filename or "").lower()
    if not filename.endswith(suffixes) or file.content_type not in content_types:
        raise HTTPException(status_code=415, detail="Unsupported import file type")
    content = await file.read(max_size + 1)
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="Import file is too large")
    return content


def _decode_csv(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must be UTF-8 encoded") from exc


def _row_error_payload(error) -> dict:
    payload = {"sheet": error.sheet, "row": error.row, "message": error.message}
    if error.code is not None:
        payload["code"] = error.code
    if error.reference_id is not None:
        payload["reference_id"] = error.reference_id
    return payload


class ProductCSVRow(BaseModel):
    brand_name: str = Field(min_length=1, max_length=100)
    product_name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    sku_code: str | None = Field(default=None, min_length=1, max_length=100)
    sku_name: str | None = Field(default=None, min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    @field_validator("category", "description", "sku_code", "sku_name", mode="before")
    @classmethod
    def empty_optional_field_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def _product_csv_row_error(
    row: int,
    *,
    code: str,
    message: str,
    exception_type: str,
) -> dict[str, object]:
    reference_id = f"imp_{uuid.uuid4().hex}"
    logger.warning(
        "Product CSV row rejected row=%d error_code=%s exception_type=%s reference_id=%s",
        row,
        code,
        exception_type,
        reference_id,
    )
    return {
        "code": code,
        "message": message,
        "reference_id": reference_id,
    }


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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
    _permission: None = Depends(require_permission("product:create")),
    _capacity_lease: XLSXParseLease = Depends(_reserve_excel_parse_capacity, scope="function"),
):
    """上传 Excel 文件进行多 Sheet 导入（品牌 → 产品 → SKU → 批次）"""
    # 校验文件类型
    content = await _read_upload(
        file,
        max_size=MAX_EXCEL_SIZE,
        suffixes=(".xlsx",),
        content_types={
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    )

    service = ExcelImportService()

    # 解析与校验
    if isinstance(_capacity_lease, XLSXParseLease):
        try:
            parsed = await service.parse_and_validate(content, tenant_id, capacity_lease=_capacity_lease)
        finally:
            _capacity_lease.release()
    else:
        # Preserve direct service-call tests; registered HTTP routes always
        # receive a validated lease from the dependency above.
        parsed = await service.parse_and_validate(content, tenant_id)
    if parsed.errors:
        # 存在文件级解析错误时直接返回
        parse_errors = [_row_error_payload(error) for error in parsed.errors]
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
    await write_audit_log(
        db,
        str(account_id),
        str(tenant_id),
        "catalog_import_completed",
        f"catalog_import:{uuid7()}",
        {
            "created": report.total_created,
            "updated": report.total_updated,
            "errors": report.total_errors,
            "import_type": "excel",
        },
    )

    # 构建错误信息
    all_errors = []
    for err in report.brands.errors:
        all_errors.append(_row_error_payload(err))
    for err in report.products.errors:
        all_errors.append(_row_error_payload(err))
    for err in report.skus.errors:
        all_errors.append(_row_error_payload(err))
    for err in report.batches.errors:
        all_errors.append(_row_error_payload(err))

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
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
    _permission: None = Depends(require_permission("product:create")),
    _rate_limit: None = Depends(_enforce_import_rate_limit),
):
    """CSV 批量导入产品"""
    content = await _read_upload(
        file,
        max_size=MAX_CSV_SIZE,
        suffixes=(".csv",),
        content_types={"text/csv", "application/csv", "application/vnd.ms-excel"},
    )
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    failed = 0
    errors: list[dict[str, object]] = []
    for row_num, row in enumerate(reader, start=2):
        if row_num > MAX_CSV_ROWS + 1:
            failed += 1
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append(
                    _product_csv_row_error(
                        row_num,
                        code="IMPORT_ROW_LIMIT_EXCEEDED",
                        message="商品导入行数超过限制",
                        exception_type="RowLimitExceeded",
                    )
                )
            break
        try:
            parsed_row = ProductCSVRow.model_validate(row)
        except ValidationError as exc:
            failed += 1
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append(
                    _product_csv_row_error(
                        row_num,
                        code="IMPORT_ROW_INVALID",
                        message="该行商品数据无效",
                        exception_type=type(exc).__name__,
                    )
                )
            continue

        try:
            async with db.begin_nested():
                # 查找或创建品牌
                from sqlalchemy import select

                result = await db.execute(
                    select(Brand).where(
                        Brand.tenant_id == tenant_id,
                        Brand.name == parsed_row.brand_name,
                    )
                )
                brand = result.scalar_one_or_none()
                if not brand:
                    brand = Brand(tenant_id=tenant_id, name=parsed_row.brand_name)
                    db.add(brand)
                    await db.flush()

                await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
                product = Product(
                    tenant_id=tenant_id,
                    brand_id=brand.id,
                    name=parsed_row.product_name,
                    category=parsed_row.category,
                    description=parsed_row.description,
                )
                db.add(product)
                await db.flush()

                # 创建 SKU
                sku_code = parsed_row.sku_code or f"SKU-{product.id.hex[:8]}"
                sku = SKU(
                    tenant_id=tenant_id,
                    product_id=product.id,
                    code=sku_code,
                    name=parsed_row.sku_name or "默认规格",
                )
                db.add(sku)
                await db.flush()
            imported += 1
        except AppException:
            raise
        except SQLAlchemyError as exc:
            failed += 1
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append(
                    _product_csv_row_error(
                        row_num,
                        code="IMPORT_ROW_FAILED",
                        message="该行商品导入失败",
                        exception_type=type(exc).__name__,
                    )
                )
        except Exception as exc:
            failed += 1
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append(
                    _product_csv_row_error(
                        row_num,
                        code="IMPORT_ROW_FAILED",
                        message="该行商品导入失败",
                        exception_type=type(exc).__name__,
                    )
                )

    await write_audit_log(
        db,
        str(account_id),
        str(tenant_id),
        "catalog_import_completed",
        f"catalog_import:{uuid7()}",
        {"created": imported, "updated": 0, "errors": failed, "import_type": "csv"},
    )
    return {"imported": imported, "errors": errors}


@import_router.post("/existing-codes")
async def import_existing_codes(
    file: UploadFile = File(...),
    code_batch_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("admin", "operator")),
    _permission: None = Depends(require_permission("code:generate")),
    _rate_limit: None = Depends(_enforce_import_rate_limit),
):
    """CSV 导入既有码（接管已有印刷码），幂等保护"""
    from sqlalchemy import select

    from app.schemas.code import ExistingCodeImportResponse

    if not code_batch_id:
        raise HTTPException(status_code=400, detail="code_batch_id is required")

    production_batch_id = await db.scalar(
        select(CodeBatch.production_batch_id).where(
            CodeBatch.id == code_batch_id,
            CodeBatch.tenant_id == tenant_id,
        )
    )
    if production_batch_id is None:
        raise HTTPException(status_code=404, detail="Code batch not found")

    production_batch = await db.scalar(
        select(ProductionBatch)
        .where(
            ProductionBatch.id == production_batch_id,
            ProductionBatch.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if production_batch is None:
        raise HTTPException(status_code=409, detail="Code batch production batch is unavailable")

    batch = await db.scalar(
        select(CodeBatch)
        .where(
            CodeBatch.id == code_batch_id,
            CodeBatch.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="Code batch not found")
    if (
        batch.production_batch_id != production_batch.id
        or batch.product_id != production_batch.product_id
        or batch.sku_id != production_batch.sku_id
    ):
        raise HTTPException(status_code=409, detail="Code batch production batch association changed")
    if batch.status != CodeBatchStatus.completed:
        raise HTTPException(status_code=409, detail="Code batch is not open for existing-code import")
    if not is_production_batch_effectively_active(production_batch):
        raise HTTPException(status_code=409, detail="Production batch is not active")

    content = await _read_upload(
        file,
        max_size=MAX_CSV_SIZE,
        suffixes=(".csv",),
        content_types={"text/csv", "application/csv", "application/vnd.ms-excel"},
    )
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))

    imported = 0
    skipped = 0
    failed = 0
    total = 0
    errors: list[dict] = []

    for row_num, row in enumerate(reader, start=2):
        if row_num > MAX_CSV_ROWS + 1:
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append({"row": row_num, "message": "row limit exceeded"})
            failed += 1
            break
        total += 1
        public_id = row.get("public_id", "").strip()
        if not public_id or not validate_public_id(public_id):
            failed += 1
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append({"row": row_num, "message": "invalid public_id"})
            continue

        try:
            existing = await db.execute(
                select(CodeItem.id)
                .where(
                    CodeItem.public_id == public_id,
                    CodeItem.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if existing.scalar_one_or_none() is not None:
                skipped += 1
                continue

            async with db.begin_nested():
                await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_CODES)
                item = CodeItem(
                    tenant_id=tenant_id,
                    code_batch_id=code_batch_id,
                    public_id=public_id,
                    status=CodeItemStatus.activated,
                    code_type=batch.code_type,
                )
                db.add(item)
                await db.flush()
            imported += 1
        except IntegrityError:
            # A concurrent import may win the globally unique public_id race.
            # The nested rollback also releases this row's reservation.
            skipped += 1
        except AppException:
            raise
        except (KeyError, TypeError, ValueError):
            failed += 1
            if len(errors) < MAX_IMPORT_ERRORS:
                errors.append({"row": row_num, "message": "invalid code data"})

    await write_audit_log(
        db,
        str(account_id),
        str(tenant_id),
        "code_import_completed",
        f"code_batch:{code_batch_id}",
        {"created": imported, "skipped": skipped, "errors": failed},
    )
    return ExistingCodeImportResponse(
        imported=imported,
        skipped=skipped,
        failed=failed,
        total=total,
        batch_id=str(code_batch_id),
        errors=errors,
    )
