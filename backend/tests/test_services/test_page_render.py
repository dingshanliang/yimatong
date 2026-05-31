"""A5-005: 模板渲染引擎测试"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import PageTemplate, PageVersion, PageVersionStatus
from app.services.page_render import _render_cache, invalidate_cache, render_page


@pytest.fixture(autouse=True)
def clear_cache():
    _render_cache._mem_store.clear()
    yield
    _render_cache._mem_store.clear()


async def _setup_published_template(db: AsyncSession, template_type: str, config: dict):
    """创建模板+版本并发布"""
    tid = uuid.uuid4()
    template = PageTemplate(
        tenant_id=tid,
        name=f"渲染测试-{template_type}",
        template_type=template_type,
    )
    db.add(template)
    await db.flush()

    version = PageVersion(
        tenant_id=tid,
        page_template_id=template.id,
        version=1,
        config_json=config,
        status=PageVersionStatus.published,
        created_by=uuid.uuid4(),
    )
    db.add(version)
    await db.flush()

    return tid, template.id, version.id


class TestPageRender:
    @pytest.mark.anyio
    async def test_render_product_info(self, db: AsyncSession):
        tid, tmpl_id, _ = await _setup_published_template(
            db,
            "product_info",
            {
                "brand_name": "测试品牌",
                "product_name": "有机苹果",
                "specifications": {"weight": "500g", "origin": "山东"},
            },
        )
        html = await render_page(db, tid, tmpl_id)
        assert html is not None
        assert "测试品牌" in html
        assert "有机苹果" in html
        assert "500g" in html

    @pytest.mark.anyio
    async def test_render_traceability(self, db: AsyncSession):
        tid, tmpl_id, _ = await _setup_published_template(
            db,
            "traceability",
            {
                "brand_name": "溯源品牌",
                "product_name": "溯源产品",
                "trace_nodes": [
                    {"name": "种植", "location": "山东", "date": "2026-01"},
                ],
            },
        )
        html = await render_page(db, tid, tmpl_id)
        assert html is not None
        assert "溯源品牌" in html
        assert "种植" in html

    @pytest.mark.anyio
    async def test_render_brand_story(self, db: AsyncSession):
        tid, tmpl_id, _ = await _setup_published_template(
            db,
            "brand_story",
            {
                "brand_name": "故事品牌",
                "story_title": "我们的故事",
                "story_content": "从田间到餐桌的旅程",
            },
        )
        html = await render_page(db, tid, tmpl_id)
        assert html is not None
        assert "故事品牌" in html
        assert "我们的故事" in html
        assert "从田间到餐桌" in html

    @pytest.mark.anyio
    async def test_render_no_published_version(self, db: AsyncSession):
        tid = uuid.uuid4()
        template = PageTemplate(
            tenant_id=tid,
            name="未发布模板",
            template_type="product_info",
        )
        db.add(template)
        await db.flush()

        html = await render_page(db, tid, template.id)
        assert html is None

    @pytest.mark.anyio
    async def test_render_caches_result(self, db: AsyncSession):
        tid, tmpl_id, _ = await _setup_published_template(
            db,
            "product_info",
            {
                "brand_name": "缓存品牌",
                "product_name": "缓存产品",
            },
        )
        html1 = await render_page(db, tid, tmpl_id)
        html2 = await render_page(db, tid, tmpl_id)
        assert html1 == html2
        cached = await _render_cache.get(f"page:{tmpl_id}")
        assert cached is not None

    @pytest.mark.anyio
    async def test_invalidate_cache(self, db: AsyncSession):
        tid, tmpl_id, _ = await _setup_published_template(
            db,
            "product_info",
            {
                "brand_name": "缓存品牌",
                "product_name": "缓存产品",
            },
        )
        await render_page(db, tid, tmpl_id)
        cached = await _render_cache.get(f"page:{tmpl_id}")
        assert cached is not None

        await invalidate_cache(tmpl_id)
        cached_after = await _render_cache.get(f"page:{tmpl_id}")
        assert cached_after is None

    @pytest.mark.anyio
    async def test_render_with_extra_context(self, db: AsyncSession):
        tid, tmpl_id, _ = await _setup_published_template(
            db,
            "product_info",
            {
                "brand_name": "品牌",
                "product_name": "产品",
            },
        )
        html = await render_page(
            db,
            tid,
            tmpl_id,
            context={"extra_key": "extra_value"},
        )
        assert html is not None
        assert "品牌" in html
