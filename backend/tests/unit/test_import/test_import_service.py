"""ERP 导入服务单元测试"""

import io
import uuid
from datetime import UTC, datetime

import pytest
from openpyxl import Workbook

from app.core.exceptions import QuotaExceededError
from app.models.plan import TenantQuotaUsage
from app.models.tenant import Tenant
from app.services.import_service import ExcelImportService, ImportResult, ParsedBrand, ParsedProduct
from app.services.quota import QUOTA_RECONCILIATION_SOURCE_REVISION


@pytest.fixture
def tenant_id():
    return uuid.uuid4()


@pytest.fixture
def service():
    return ExcelImportService()


def _make_brand_sheet(wb, rows):
    ws = wb.create_sheet("品牌")
    ws.append(["品牌名称*", "品牌Logo URL", "品牌描述", "external_id"])
    for row in rows:
        ws.append(row)


def _make_product_sheet(wb, rows):
    ws = wb.create_sheet("产品")
    ws.append(["品牌(名称)*", "产品名称*", "产品分类", "产品描述", "external_id"])
    for row in rows:
        ws.append(row)


def _make_sku_sheet(wb, rows):
    ws = wb.create_sheet("SKU")
    ws.append(["产品(名称)*", "SKU编码*", "SKU名称*", "规格(JSON)", "external_id"])
    for row in rows:
        ws.append(row)


def _make_batch_sheet(wb, rows):
    ws = wb.create_sheet("批次")
    ws.append(["SKU(编码)*", "批次号*", "生产日期*", "有效期至*", "external_id"])
    for row in rows:
        ws.append(row)


class TestTemplateGeneration:
    def test_generate_template_has_all_sheets(self, service):
        content = service.generate_template()
        assert content is not None

        from openpyxl import load_workbook

        if isinstance(content, io.BytesIO):
            content = content.getvalue()
        wb = load_workbook(io.BytesIO(content))
        assert "品牌" in wb.sheetnames
        assert "产品" in wb.sheetnames
        assert "SKU" in wb.sheetnames
        assert "批次" in wb.sheetnames


class TestExcelParsing:
    async def test_parse_minimal_excel(self, service, db, tenant_id):
        wb = Workbook()
        wb.remove(wb.active)

        _make_brand_sheet(wb, [["测试品牌", "", "品牌描述", ""]])
        _make_product_sheet(wb, [["测试品牌", "测试产品", "零食", "产品描述", ""]])
        _make_sku_sheet(wb, [["测试产品", "SKU-001", "500g装", "{}", ""]])
        _make_batch_sheet(wb, [["SKU-001", "BH001", "2026-01-01", "2027-01-01", ""]])

        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()

        result = await service.parse_and_validate(content, tenant_id)
        assert len(result.errors) == 0
        assert len(result.brands) == 1
        assert len(result.products) == 1
        assert len(result.skus) == 1
        assert len(result.batches) == 1

    async def test_parse_empty_required_field_skipped(self, service, db, tenant_id):
        wb = Workbook()
        wb.remove(wb.active)

        _make_brand_sheet(wb, [["", "", "", ""]])  # 品牌名称为空 → 跳过
        _make_product_sheet(wb, [])
        _make_sku_sheet(wb, [])
        _make_batch_sheet(wb, [])

        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()

        result = await service.parse_and_validate(content, tenant_id)
        assert len(result.brands) == 0

    async def test_parse_with_external_id(self, service, db, tenant_id):
        wb = Workbook()
        wb.remove(wb.active)

        _make_brand_sheet(wb, [["测试品牌", "", "", "ext_brand_001"]])
        _make_product_sheet(wb, [])
        _make_sku_sheet(wb, [])
        _make_batch_sheet(wb, [])

        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()

        result = await service.parse_and_validate(content, tenant_id)
        assert len(result.brands) == 1
        assert result.brands[0].external_id == "ext_brand_001"

    async def test_parse_multiple_brands(self, service, db, tenant_id):
        wb = Workbook()
        wb.remove(wb.active)

        _make_brand_sheet(
            wb,
            [
                ["品牌A", "", "描述A", "ext_a"],
                ["品牌B", "", "描述B", "ext_b"],
            ],
        )
        _make_product_sheet(wb, [])
        _make_sku_sheet(wb, [])
        _make_batch_sheet(wb, [])

        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()

        result = await service.parse_and_validate(content, tenant_id)
        assert len(result.brands) == 2


class TestExcelExecutionQuota:
    async def test_created_product_is_reserved_once_and_upsert_update_is_not_recounted(self, service, db, tenant_id):
        db.add(
            Tenant(
                id=tenant_id,
                name="Excel 配额租户",
                slug=f"excel-quota-{tenant_id.hex[:8]}",
                quota={"max_products": 1},
            )
        )
        db.add(
            TenantQuotaUsage(
                tenant_id=tenant_id,
                reconciled_at=datetime.now(UTC),
                source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
                enforcement_ready=True,
            )
        )
        parsed = ImportResult(
            brands=[ParsedBrand(2, "导入品牌", None, None, "brand-1")],
            products=[ParsedProduct(2, "导入品牌", "导入产品", None, None, "product-1")],
        )

        first = await service.execute_import(parsed, tenant_id, db)
        second = await service.execute_import(parsed, tenant_id, db)

        usage = await db.get(TenantQuotaUsage, tenant_id)
        assert usage is not None
        assert usage.products == 1
        assert first.products.created == 1
        assert second.products.updated == 1

    async def test_quota_error_aborts_excel_import_instead_of_becoming_row_error(self, service, db, tenant_id):
        db.add(
            Tenant(
                id=tenant_id,
                name="Excel 超额租户",
                slug=f"excel-overage-{tenant_id.hex[:8]}",
                quota={"max_products": 0},
            )
        )
        db.add(
            TenantQuotaUsage(
                tenant_id=tenant_id,
                reconciled_at=datetime.now(UTC),
                source_revision=QUOTA_RECONCILIATION_SOURCE_REVISION,
                enforcement_ready=True,
            )
        )
        parsed = ImportResult(
            brands=[ParsedBrand(2, "导入品牌", None, None, "brand-2")],
            products=[ParsedProduct(2, "导入品牌", "超额产品", None, None, "product-2")],
        )

        with pytest.raises(QuotaExceededError):
            await service.execute_import(parsed, tenant_id, db)
