"""A5-001: PageTemplate 与 PageVersion 数据模型测试"""

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import PageTemplate, PageTemplateStatus, PageVersion, PageVersionStatus


class TestPageTemplate:
    @pytest.mark.anyio
    async def test_create_page_template(self, db: AsyncSession):
        t = PageTemplate(
            tenant_id=uuid.uuid4(),
            name="产品信息页",
            template_type="product_info",
            description="展示产品基本信息的固定模板",
        )
        db.add(t)
        await db.flush()
        await db.refresh(t)
        assert t.id is not None
        assert t.name == "产品信息页"
        assert t.template_type == "product_info"
        assert t.status == PageTemplateStatus.active
        assert t.description is not None

    @pytest.mark.anyio
    async def test_page_template_has_required_fields(self):
        mapper = inspect(PageTemplate)
        col_names = {c.key for c in mapper.mapper.column_attrs}
        required = {"id", "tenant_id", "name", "template_type", "status", "description"}
        assert required.issubset(col_names)

    @pytest.mark.anyio
    async def test_template_type_values(self):
        assert PageTemplateStatus.active == "active"
        assert PageTemplateStatus.archived == "archived"


class TestPageVersion:
    @pytest.mark.anyio
    async def test_create_page_version(self, db: AsyncSession):
        template = PageTemplate(
            tenant_id=uuid.uuid4(),
            name="模板",
            template_type="product_info",
        )
        db.add(template)
        await db.flush()

        v = PageVersion(
            tenant_id=template.tenant_id,
            page_template_id=template.id,
            version=1,
            config_json={"brand_name": "测试品牌"},
            created_by=uuid.uuid4(),
        )
        db.add(v)
        await db.flush()
        await db.refresh(v)
        assert v.version == 1
        assert v.config_json["brand_name"] == "测试品牌"
        assert v.status == PageVersionStatus.draft

    @pytest.mark.anyio
    async def test_page_version_has_required_fields(self):
        mapper = inspect(PageVersion)
        col_names = {c.key for c in mapper.mapper.column_attrs}
        required = {"id", "tenant_id", "page_template_id", "version", "config_json", "status", "created_by"}
        assert required.issubset(col_names)

    @pytest.mark.anyio
    async def test_version_status_values(self):
        assert PageVersionStatus.draft == "draft"
        assert PageVersionStatus.published == "published"
        assert PageVersionStatus.archived == "archived"
