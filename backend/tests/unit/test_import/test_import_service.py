"""ERP 导入服务单元测试"""

import io
import uuid

import pytest
from openpyxl import Workbook

from app.services.import_service import ExcelImportService


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
