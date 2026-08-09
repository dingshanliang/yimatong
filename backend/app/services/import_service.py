"""Excel 多 Sheet 导入服务：品牌 → 产品 → SKU → 批次"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.integration import SyncRecord
from app.models.product import SKU, Brand, Product, ProductionBatch
from app.services.quota import CumulativeQuotaKey, reserve_quota

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "csv_import"

# ---------------------------------------------------------------------------
# Pydantic 行校验模型
# ---------------------------------------------------------------------------


class BrandRow(BaseModel):
    brand_name: str = Field(min_length=1, max_length=100, alias="品牌名称*")
    logo_url: str | None = Field(None, max_length=500, alias="品牌Logo URL")
    description: str | None = Field(None, max_length=500, alias="品牌描述")
    external_id: str | None = Field(None, max_length=100, alias="external_id")

    model_config = {"populate_by_name": True}


class ProductRow(BaseModel):
    brand_name: str = Field(min_length=1, max_length=100, alias="品牌(名称)*")
    product_name: str = Field(min_length=1, max_length=200, alias="产品名称*")
    category: str | None = Field(None, max_length=100, alias="产品分类")
    description: str | None = Field(None, max_length=1000, alias="产品描述")
    external_id: str | None = Field(None, max_length=100, alias="external_id")

    model_config = {"populate_by_name": True}


class SKURow(BaseModel):
    product_name: str = Field(min_length=1, max_length=200, alias="产品(名称)*")
    sku_code: str = Field(min_length=1, max_length=100, alias="SKU编码*")
    sku_name: str = Field(min_length=1, max_length=200, alias="SKU名称*")
    specifications: str | None = Field(None, alias="规格(JSON)")
    external_id: str | None = Field(None, max_length=100, alias="external_id")

    model_config = {"populate_by_name": True}

    @field_validator("specifications", mode="before")
    @classmethod
    def validate_spec_json(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        try:
            json.loads(v)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"规格必须是合法 JSON: {exc}") from exc
        return v


class BatchRow(BaseModel):
    sku_code: str = Field(min_length=1, max_length=100, alias="SKU(编码)*")
    batch_code: str = Field(min_length=1, max_length=100, alias="批次号*")
    production_date: date = Field(alias="生产日期*")
    expiry_date: date = Field(alias="有效期至*")
    external_id: str | None = Field(None, max_length=100, alias="external_id")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class RowError:
    sheet: str
    row: int
    message: str


@dataclass
class ParsedBrand:
    row_num: int
    brand_name: str
    logo_url: str | None
    description: str | None
    external_id: str | None


@dataclass
class ParsedProduct:
    row_num: int
    brand_name: str
    product_name: str
    category: str | None
    description: str | None
    external_id: str | None


@dataclass
class ParsedSKU:
    row_num: int
    product_name: str
    sku_code: str
    sku_name: str
    specifications: dict | None
    external_id: str | None


@dataclass
class ParsedBatch:
    row_num: int
    sku_code: str
    batch_code: str
    production_date: date
    expiry_date: date
    external_id: str | None


@dataclass
class ImportResult:
    brands: list[ParsedBrand] = field(default_factory=list)
    products: list[ParsedProduct] = field(default_factory=list)
    skus: list[ParsedSKU] = field(default_factory=list)
    batches: list[ParsedBatch] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(self.brands or self.products or self.skus or self.batches)


@dataclass
class SheetReport:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)


@dataclass
class ImportReport:
    brands: SheetReport = field(default_factory=SheetReport)
    products: SheetReport = field(default_factory=SheetReport)
    skus: SheetReport = field(default_factory=SheetReport)
    batches: SheetReport = field(default_factory=SheetReport)

    @property
    def total_created(self) -> int:
        return self.brands.created + self.products.created + self.skus.created + self.batches.created

    @property
    def total_updated(self) -> int:
        return self.brands.updated + self.products.updated + self.skus.updated + self.batches.updated

    @property
    def total_errors(self) -> int:
        return len(self.brands.errors) + len(self.products.errors) + len(self.skus.errors) + len(self.batches.errors)


# ---------------------------------------------------------------------------
# Excel 列定义
# ---------------------------------------------------------------------------

# 每个 Sheet 的列定义：(header_text, field_key, width, comment)
BRAND_COLUMNS = [
    ("品牌名称*", "brand_name", 25, "必填，最长 100 字符"),
    ("品牌Logo URL", "logo_url", 35, "可选，品牌 Logo 图片地址"),
    ("品牌描述", "description", 40, "可选，品牌描述文字"),
    ("external_id", "external_id", 25, "可选，外部系统 ID，填写后将按此字段匹配更新"),
]

PRODUCT_COLUMNS = [
    ("品牌(名称)*", "brand_name", 25, "必填，需与品牌 Sheet 中的名称一致"),
    ("产品名称*", "product_name", 30, "必填，最长 200 字符"),
    ("产品分类", "category", 20, "可选，产品分类名称"),
    ("产品描述", "description", 40, "可选，产品描述文字"),
    ("external_id", "external_id", 25, "可选，外部系统 ID"),
]

SKU_COLUMNS = [
    ("产品(名称)*", "product_name", 30, "必填，需与产品 Sheet 中的名称一致"),
    ("SKU编码*", "sku_code", 25, "必填，SKU 唯一编码"),
    ("SKU名称*", "sku_name", 25, "必填，SKU 显示名称"),
    ("规格(JSON)", "specifications", 35, '可选，JSON 格式，如 {"重量":"500g","颜色":"红"}'),
    ("external_id", "external_id", 25, "可选，外部系统 ID"),
]

BATCH_COLUMNS = [
    ("SKU(编码)*", "sku_code", 25, "必填，需与 SKU Sheet 中的编码一致"),
    ("批次号*", "batch_code", 25, "必填，生产批次号"),
    ("生产日期*", "production_date", 20, "必填，格式 YYYY-MM-DD"),
    ("有效期至*", "expiry_date", 20, "必填，格式 YYYY-MM-DD"),
    ("external_id", "external_id", 25, "可选，外部系统 ID"),
]

SHEET_DEFS = {
    "品牌": BRAND_COLUMNS,
    "产品": PRODUCT_COLUMNS,
    "SKU": SKU_COLUMNS,
    "批次": BATCH_COLUMNS,
}

# 每个 Sheet 的示例数据
EXAMPLE_ROWS: dict[str, list[list[str | None]]] = {
    "品牌": [
        ["示例品牌", "https://example.com/logo.png", "这是一个示例品牌描述", "BRAND-001"],
    ],
    "产品": [
        ["示例品牌", "示例产品", "零食", "示例产品描述", "PROD-001"],
    ],
    "SKU": [
        ["示例产品", "SKU-001", "500g 装", '{"重量":"500g"}', "SKU-EXT-001"],
    ],
    "批次": [
        ["SKU-001", "BATCH-20260101", "2026-01-01", "2027-01-01", "BATCH-EXT-001"],
    ],
}


# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------


class ExcelImportService:
    """Excel 多 Sheet 导入服务"""

    # --- 模板生成 ---

    @staticmethod
    def generate_template() -> BytesIO:
        """生成多 Sheet Excel 导入模板"""
        wb = Workbook()
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font_white = Font(bold=True, size=11, color="FFFFFF")
        example_font = Font(italic=True, color="808080", size=10)

        first = True
        for sheet_name, columns in SHEET_DEFS.items():
            if first:
                ws = wb.active
                ws.title = sheet_name
                first = False
            else:
                ws = wb.create_sheet(title=sheet_name)

            # 写表头
            for col_idx, (header, _key, width, comment) in enumerate(columns, start=1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = header_font_white
                cell.fill = header_fill
                cell.comment = __import__("openpyxl.comments", fromlist=["Comment"]).Comment(comment, "系统")
                ws.column_dimensions[get_column_letter(col_idx)].width = width

            # 写示例行
            examples = EXAMPLE_ROWS.get(sheet_name, [])
            for row_offset, example in enumerate(examples, start=2):
                for col_idx, value in enumerate(example, start=1):
                    cell = ws.cell(row=row_offset, column=col_idx, value=value)
                    cell.font = example_font

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # --- 解析与校验 ---

    async def parse_and_validate(self, file_content: bytes, tenant_id: uuid.UUID) -> ImportResult:
        """解析多 Sheet Excel，校验每一行，返回结构化结果"""
        result = ImportResult()

        # File size limit: 10 MB
        max_file_size = 10 * 1024 * 1024
        if len(file_content) > max_file_size:
            result.errors.append(
                RowError(sheet="文件", row=0, message=f"文件大小超过限制（最大 {max_file_size // (1024 * 1024)} MB）")
            )
            return result

        try:
            wb = load_workbook(BytesIO(file_content), data_only=True, read_only=True)
        except Exception as exc:
            result.errors.append(RowError(sheet="文件", row=0, message=f"无法解析 Excel 文件: {exc}"))
            return result

        # 按顺序解析各 Sheet
        if "品牌" in wb.sheetnames:
            brands, errs = await self._parse_sheet_brands(wb["品牌"])
            result.brands = brands
            result.errors.extend(errs)

        if "产品" in wb.sheetnames:
            # 构建品牌名称映射（包括本次新导入的品牌 + 数据库已有品牌）
            brand_name_map: dict[str, str] = {}
            for b in result.brands:
                brand_name_map[b.brand_name] = b.brand_name  # 占位，后面执行时才真正查找 ID
            products, errs = await self._parse_sheet_products(wb["产品"])
            result.products = products
            result.errors.extend(errs)

        if "SKU" in wb.sheetnames:
            skus, errs = await self._parse_sheet_skus(wb["SKU"])
            result.skus = skus
            result.errors.extend(errs)

        if "批次" in wb.sheetnames:
            batches, errs = await self._parse_sheet_batches(wb["批次"])
            result.batches = batches
            result.errors.extend(errs)

        wb.close()
        return result

    async def _parse_sheet_brands(self, ws) -> tuple[list[ParsedBrand], list[RowError]]:
        """解析品牌 Sheet"""
        rows: list[ParsedBrand] = []
        errors: list[RowError] = []

        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col_map = self._build_column_map(headers, BRAND_COLUMNS)

        for row_idx, raw in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(raw):
                continue  # 跳过空行
            try:
                values = self._map_row(raw, col_map, len(BRAND_COLUMNS))
                parsed = BrandRow(
                    brand_name=self._str(values[0]),
                    logo_url=self._str_or_none(values[1]),
                    description=self._str_or_none(values[2]),
                    external_id=self._str_or_none(values[3]),
                )
                rows.append(
                    ParsedBrand(
                        row_num=row_idx,
                        brand_name=parsed.brand_name,
                        logo_url=parsed.logo_url,
                        description=parsed.description,
                        external_id=parsed.external_id,
                    )
                )
            except Exception as exc:
                errors.append(RowError(sheet="品牌", row=row_idx, message=str(exc)))

        return rows, errors

    async def _parse_sheet_products(self, ws) -> tuple[list[ParsedProduct], list[RowError]]:
        """解析产品 Sheet"""
        rows: list[ParsedProduct] = []
        errors: list[RowError] = []

        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col_map = self._build_column_map(headers, PRODUCT_COLUMNS)

        for row_idx, raw in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(raw):
                continue
            try:
                values = self._map_row(raw, col_map, len(PRODUCT_COLUMNS))
                parsed = ProductRow(
                    brand_name=self._str(values[0]),
                    product_name=self._str(values[1]),
                    category=self._str_or_none(values[2]),
                    description=self._str_or_none(values[3]),
                    external_id=self._str_or_none(values[4]),
                )
                rows.append(
                    ParsedProduct(
                        row_num=row_idx,
                        brand_name=parsed.brand_name,
                        product_name=parsed.product_name,
                        category=parsed.category,
                        description=parsed.description,
                        external_id=parsed.external_id,
                    )
                )
            except Exception as exc:
                errors.append(RowError(sheet="产品", row=row_idx, message=str(exc)))

        return rows, errors

    async def _parse_sheet_skus(self, ws) -> tuple[list[ParsedSKU], list[RowError]]:
        """解析 SKU Sheet"""
        rows: list[ParsedSKU] = []
        errors: list[RowError] = []

        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col_map = self._build_column_map(headers, SKU_COLUMNS)

        for row_idx, raw in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(raw):
                continue
            try:
                values = self._map_row(raw, col_map, len(SKU_COLUMNS))
                spec_str = self._str_or_none(values[3])
                spec_dict = json.loads(spec_str) if spec_str else None

                parsed = SKURow(
                    product_name=self._str(values[0]),
                    sku_code=self._str(values[1]),
                    sku_name=self._str(values[2]),
                    specifications=spec_str,
                    external_id=self._str_or_none(values[4]),
                )
                rows.append(
                    ParsedSKU(
                        row_num=row_idx,
                        product_name=parsed.product_name,
                        sku_code=parsed.sku_code,
                        sku_name=parsed.sku_name,
                        specifications=spec_dict,
                        external_id=parsed.external_id,
                    )
                )
            except Exception as exc:
                errors.append(RowError(sheet="SKU", row=row_idx, message=str(exc)))

        return rows, errors

    async def _parse_sheet_batches(self, ws) -> tuple[list[ParsedBatch], list[RowError]]:
        """解析批次 Sheet"""
        rows: list[ParsedBatch] = []
        errors: list[RowError] = []

        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col_map = self._build_column_map(headers, BATCH_COLUMNS)

        for row_idx, raw in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(raw):
                continue
            try:
                values = self._map_row(raw, col_map, len(BATCH_COLUMNS))
                prod_date = self._parse_date(values[2])
                exp_date = self._parse_date(values[3])
                if prod_date is None:
                    raise ValueError("生产日期格式错误，请使用 YYYY-MM-DD 格式")
                if exp_date is None:
                    raise ValueError("有效期至格式错误，请使用 YYYY-MM-DD 格式")

                parsed = BatchRow(
                    sku_code=self._str(values[0]),
                    batch_code=self._str(values[1]),
                    production_date=prod_date,
                    expiry_date=exp_date,
                    external_id=self._str_or_none(values[4]),
                )
                rows.append(
                    ParsedBatch(
                        row_num=row_idx,
                        sku_code=parsed.sku_code,
                        batch_code=parsed.batch_code,
                        production_date=parsed.production_date,
                        expiry_date=parsed.expiry_date,
                        external_id=parsed.external_id,
                    )
                )
            except Exception as exc:
                errors.append(RowError(sheet="批次", row=row_idx, message=str(exc)))

        return rows, errors

    # --- Upsert 执行 ---

    async def execute_import(
        self,
        parsed: ImportResult,
        tenant_id: uuid.UUID,
        db: AsyncSession,
        account_id: uuid.UUID | None = None,
    ) -> ImportReport:
        """执行导入，按 品牌 → 产品 → SKU → 批次 顺序，支持 upsert"""
        report = ImportReport()

        # --- 1. 品牌导入 ---
        brand_name_to_id: dict[str, uuid.UUID] = {}
        for item in parsed.brands:
            report.brands.total += 1
            try:
                brand = await self._upsert_brand(db, tenant_id, item)
                if brand is not None:
                    brand_name_to_id[item.brand_name] = brand.id
                    if brand.external_id and brand.external_id == item.external_id:
                        # 通过 external_id 匹配到的算更新
                        report.brands.updated += 1
                    else:
                        report.brands.created += 1
                    await self._write_sync_record(
                        db,
                        tenant_id,
                        "brand_import",
                        item.external_id,
                        {
                            "action": "upsert",
                            "brand_name": item.brand_name,
                            "external_id": item.external_id,
                            "row": item.row_num,
                        },
                    )
                else:
                    report.brands.skipped += 1
            except Exception as exc:
                report.brands.errors.append(RowError(sheet="品牌", row=item.row_num, message=str(exc)))
                logger.warning("品牌导入第 %d 行失败: %s", item.row_num, exc)

        await db.flush()

        # --- 2. 产品导入 ---
        product_name_to_id: dict[str, uuid.UUID] = {}
        for item in parsed.products:
            report.products.total += 1
            try:
                brand_id = brand_name_to_id.get(item.brand_name)
                if brand_id is None:
                    # 尝试从数据库查找
                    result = await db.execute(
                        select(Brand).where(Brand.tenant_id == tenant_id, Brand.name == item.brand_name)
                    )
                    brand = result.scalar_one_or_none()
                    if brand is None:
                        msg = f"品牌 '{item.brand_name}' 不存在，请先在品牌 Sheet 中添加"
                        report.products.errors.append(RowError(sheet="产品", row=item.row_num, message=msg))
                        continue
                    brand_id = brand.id
                    brand_name_to_id[item.brand_name] = brand_id

                async with db.begin_nested():
                    product, created = await self._upsert_product(db, tenant_id, brand_id, item)
                    await self._write_sync_record(
                        db,
                        tenant_id,
                        "product_import",
                        item.external_id,
                        {
                            "action": "upsert",
                            "product_name": item.product_name,
                            "brand_name": item.brand_name,
                            "external_id": item.external_id,
                            "row": item.row_num,
                        },
                    )
                    await db.flush()
                product_name_to_id[item.product_name] = product.id
                if created:
                    report.products.created += 1
                else:
                    report.products.updated += 1
            except AppException:
                raise
            except Exception as exc:
                report.products.errors.append(RowError(sheet="产品", row=item.row_num, message=str(exc)))
                logger.warning("产品导入第 %d 行失败: %s", item.row_num, exc)

        await db.flush()

        # --- 3. SKU 导入 ---
        sku_code_to_id: dict[str, uuid.UUID] = {}
        for item in parsed.skus:
            report.skus.total += 1
            try:
                product_id = product_name_to_id.get(item.product_name)
                if product_id is None:
                    result = await db.execute(
                        select(Product).where(Product.tenant_id == tenant_id, Product.name == item.product_name)
                    )
                    product = result.scalar_one_or_none()
                    if product is None:
                        msg = f"产品 '{item.product_name}' 不存在，请先在产品 Sheet 中添加"
                        report.skus.errors.append(RowError(sheet="SKU", row=item.row_num, message=msg))
                        continue
                    product_id = product.id
                    product_name_to_id[item.product_name] = product_id

                sku = await self._upsert_sku(db, tenant_id, product_id, item)
                if sku is not None:
                    sku_code_to_id[item.sku_code] = sku.id
                    if sku.external_id and sku.external_id == item.external_id:
                        report.skus.updated += 1
                    else:
                        report.skus.created += 1
                    await self._write_sync_record(
                        db,
                        tenant_id,
                        "sku_import",
                        item.external_id,
                        {
                            "action": "upsert",
                            "sku_code": item.sku_code,
                            "product_name": item.product_name,
                            "external_id": item.external_id,
                            "row": item.row_num,
                        },
                    )
                else:
                    report.skus.skipped += 1
            except Exception as exc:
                report.skus.errors.append(RowError(sheet="SKU", row=item.row_num, message=str(exc)))
                logger.warning("SKU 导入第 %d 行失败: %s", item.row_num, exc)

        await db.flush()

        # --- 4. 批次导入 ---
        for item in parsed.batches:
            report.batches.total += 1
            try:
                sku_id = sku_code_to_id.get(item.sku_code)
                if sku_id is None:
                    result = await db.execute(select(SKU).where(SKU.tenant_id == tenant_id, SKU.code == item.sku_code))
                    sku = result.scalar_one_or_none()
                    if sku is None:
                        msg = f"SKU 编码 '{item.sku_code}' 不存在，请先在 SKU Sheet 中添加"
                        report.batches.errors.append(RowError(sheet="批次", row=item.row_num, message=msg))
                        continue
                    sku_id = sku.id
                    sku_code_to_id[item.sku_code] = sku_id

                # 获取 product_id (批次表需要)
                result = await db.execute(select(SKU).where(SKU.id == sku_id))
                sku_obj = result.scalar_one()
                product_id = sku_obj.product_id

                batch = await self._upsert_batch(db, tenant_id, product_id, sku_id, item)
                if batch is not None:
                    if batch.external_id and batch.external_id == item.external_id:
                        report.batches.updated += 1
                    else:
                        report.batches.created += 1
                    await self._write_sync_record(
                        db,
                        tenant_id,
                        "batch_import",
                        item.external_id,
                        {
                            "action": "upsert",
                            "batch_code": item.batch_code,
                            "sku_code": item.sku_code,
                            "external_id": item.external_id,
                            "row": item.row_num,
                        },
                    )
                else:
                    report.batches.skipped += 1
            except Exception as exc:
                report.batches.errors.append(RowError(sheet="批次", row=item.row_num, message=str(exc)))
                logger.warning("批次导入第 %d 行失败: %s", item.row_num, exc)

        await db.flush()
        return report

    # --- Upsert 内部方法 ---

    async def _upsert_brand(self, db: AsyncSession, tenant_id: uuid.UUID, item: ParsedBrand) -> Brand | None:
        """品牌 upsert：按 external_id 查找更新或新建"""
        existing = None
        if item.external_id:
            result = await db.execute(
                select(Brand).where(
                    Brand.tenant_id == tenant_id,
                    Brand.source_system == SOURCE_SYSTEM,
                    Brand.external_id == item.external_id,
                )
            )
            existing = result.scalar_one_or_none()

        if existing:
            existing.name = item.brand_name
            if item.logo_url is not None:
                existing.logo_url = item.logo_url
            if item.description is not None:
                existing.description = item.description
            return existing

        brand = Brand(
            tenant_id=tenant_id,
            name=item.brand_name,
            logo_url=item.logo_url,
            description=item.description,
            source_system=SOURCE_SYSTEM,
            external_id=item.external_id,
        )
        db.add(brand)
        await db.flush()
        return brand

    async def _upsert_product(
        self, db: AsyncSession, tenant_id: uuid.UUID, brand_id: uuid.UUID, item: ParsedProduct
    ) -> tuple[Product, bool]:
        """产品 upsert"""
        existing = None
        if item.external_id:
            result = await db.execute(
                select(Product).where(
                    Product.tenant_id == tenant_id,
                    Product.source_system == SOURCE_SYSTEM,
                    Product.external_id == item.external_id,
                )
            )
            existing = result.scalar_one_or_none()

        if existing:
            existing.name = item.product_name
            existing.brand_id = brand_id
            if item.category is not None:
                existing.category = item.category
            if item.description is not None:
                existing.description = item.description
            return existing, False

        await reserve_quota(db, tenant_id, CumulativeQuotaKey.MAX_PRODUCTS)
        product = Product(
            tenant_id=tenant_id,
            brand_id=brand_id,
            name=item.product_name,
            category=item.category,
            description=item.description,
            source_system=SOURCE_SYSTEM,
            external_id=item.external_id,
        )
        db.add(product)
        await db.flush()
        return product, True

    async def _upsert_sku(
        self, db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID, item: ParsedSKU
    ) -> SKU | None:
        """SKU upsert"""
        existing = None
        if item.external_id:
            result = await db.execute(
                select(SKU).where(
                    SKU.tenant_id == tenant_id,
                    SKU.source_system == SOURCE_SYSTEM,
                    SKU.external_id == item.external_id,
                )
            )
            existing = result.scalar_one_or_none()

        if existing:
            existing.product_id = product_id
            existing.code = item.sku_code
            existing.name = item.sku_name
            if item.specifications is not None:
                existing.specifications = item.specifications
            return existing

        sku = SKU(
            tenant_id=tenant_id,
            product_id=product_id,
            code=item.sku_code,
            name=item.sku_name,
            specifications=item.specifications,
            source_system=SOURCE_SYSTEM,
            external_id=item.external_id,
        )
        db.add(sku)
        await db.flush()
        return sku

    async def _upsert_batch(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        product_id: uuid.UUID,
        sku_id: uuid.UUID,
        item: ParsedBatch,
    ) -> ProductionBatch | None:
        """批次 upsert"""
        existing = None
        if item.external_id:
            result = await db.execute(
                select(ProductionBatch).where(
                    ProductionBatch.tenant_id == tenant_id,
                    ProductionBatch.source_system == SOURCE_SYSTEM,
                    ProductionBatch.external_id == item.external_id,
                )
            )
            existing = result.scalar_one_or_none()

        if existing:
            existing.product_id = product_id
            existing.sku_id = sku_id
            existing.batch_code = item.batch_code
            existing.production_date = item.production_date
            existing.expiry_date = item.expiry_date
            return existing

        batch = ProductionBatch(
            tenant_id=tenant_id,
            product_id=product_id,
            sku_id=sku_id,
            batch_code=item.batch_code,
            production_date=item.production_date,
            expiry_date=item.expiry_date,
            source_system=SOURCE_SYSTEM,
            external_id=item.external_id,
        )
        db.add(batch)
        await db.flush()
        return batch

    # --- 审计日志 ---

    @staticmethod
    async def _write_sync_record(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        sync_type: str,
        external_id: str | None,
        data: dict[str, Any],
    ) -> None:
        record = SyncRecord(
            tenant_id=tenant_id,
            sync_type=sync_type,
            external_id=external_id,
            data=data,
        )
        db.add(record)

    # --- 工具方法 ---

    @staticmethod
    def _build_column_map(headers: list[str | None], columns: list[tuple]) -> dict[int, int]:
        """将 Excel 列头映射到列定义索引。返回 {excel_col_idx: definition_idx}"""
        header_to_def: dict[str, int] = {}
        for idx, (header_text, _key, _w, _c) in enumerate(columns):
            header_to_def[header_text.strip()] = idx
            # 同时支持字段 key 映射
            header_to_def[_key.strip()] = idx

        col_map: dict[int, int] = {}
        for excel_idx, header in enumerate(headers):
            if header is None:
                continue
            header_stripped = str(header).strip()
            if header_stripped in header_to_def:
                col_map[excel_idx] = header_to_def[header_stripped]
        return col_map

    @staticmethod
    def _map_row(raw: tuple, col_map: dict[int, int], field_count: int) -> list[Any]:
        """将 Excel 原始行数据按 col_map 映射为有序字段列表"""
        values: list[Any] = [None] * field_count
        for excel_idx, def_idx in col_map.items():
            if def_idx < field_count and excel_idx < len(raw):
                values[def_idx] = raw[excel_idx]
        return values

    @staticmethod
    def _str(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _str_or_none(value: Any) -> str | None:
        if value is None:
            return None
        s = str(value).strip()
        return s if s else None

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        """解析日期值，支持 date 对象、字符串 YYYY-MM-DD 格式"""
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            try:
                return date.fromisoformat(s)
            except ValueError:
                return None
        # openpyxl 有时返回 datetime
        if hasattr(value, "date"):
            return value.date()
        return None
