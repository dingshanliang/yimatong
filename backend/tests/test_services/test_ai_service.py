"""AI 资料识别与文案生成服务 单元测试"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai import (
    extract_product_fields,
    extract_product_from_image,
    generate_campaign,
    generate_copywriting,
    generate_page_copy,
    suggest_page_structure,
)


def _make_db():
    return AsyncMock()


@pytest.fixture(autouse=True)
def _mock_limits():
    with (
        patch("app.services.ai._check_daily_limit", new_callable=AsyncMock),
        patch("app.services.ai._increment_daily_count", new_callable=AsyncMock),
    ):
        yield


def _mock_save_generation(db):
    record = MagicMock(id=uuid.uuid4())
    return record


# ── extract_product_fields ──────────────────────


class TestExtractProductFields:
    """从文本提取产品字段"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.db = _make_db()

    @pytest.mark.asyncio
    async def test_extract_product_name(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": "脐橙"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_fields("这是一款优质的脐橙，来自江西赣州", uuid.uuid4(), self.db)
        assert result["fields"]["product_name"] == "脐橙"

    @pytest.mark.asyncio
    async def test_extract_origin(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": "赣南脐橙", "origin": "江西赣州", "weight": "5kg"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_fields("赣南脐橙，产地江西赣州，每箱5kg", uuid.uuid4(), self.db)
        assert result["fields"]["origin"] == "江西赣州"

    @pytest.mark.asyncio
    async def test_extract_weight(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": "大米", "weight": "5kg", "shelf_life": "12个月"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_fields("优质大米，净重5kg，保质期12个月", uuid.uuid4(), self.db)
        assert result["fields"]["weight"] == "5kg"

    @pytest.mark.asyncio
    async def test_extract_shelf_life(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": "蜂蜜", "origin": "云南", "shelf_life": "24个月"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_fields("蜂蜜 产地云南 保质期24个月", uuid.uuid4(), self.db)
        assert result["fields"]["shelf_life"] == "24个月"

    @pytest.mark.asyncio
    async def test_unknown_product(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": None}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_fields("这是一段没有明确产品名称的文字", uuid.uuid4(), self.db)
        assert "product_name" in result["fields"]

    @pytest.mark.asyncio
    async def test_multiple_fields(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {
                "product_name": "脐橙",
                "origin": "江西赣州",
                "weight": "5kg",
                "shelf_life": "6个月",
            }
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_fields("赣南脐橙，产地江西赣州，净重5kg，保质期6个月", uuid.uuid4(), self.db)
        assert result["fields"]["product_name"] == "脐橙"
        assert result["fields"]["origin"] == "江西赣州"
        assert result["fields"]["weight"] == "5kg"
        assert result["fields"]["shelf_life"] == "6个月"


# ── extract_product_from_image ──────────────────


class TestExtractProductFromImage:
    """从图片识别产品信息"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.db = _make_db()

    @pytest.mark.asyncio
    async def test_returns_product_fields(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": "脐橙", "category": "水果"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_from_image(
                image_url="https://example.com/product.jpg",
                filename="product.jpg",
                tenant_id=uuid.uuid4(),
                db=self.db,
            )
        assert "fields" in result
        assert result["fields"]["product_name"] == "脐橙"
        assert result["fields"]["category"] == "水果"

    @pytest.mark.asyncio
    async def test_png_image(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"product_name": "蜂蜜", "category": "蜂蜜"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await extract_product_from_image(
                image_url="https://example.com/product.png",
                filename="product.png",
                tenant_id=uuid.uuid4(),
                db=self.db,
            )
        assert "fields" in result


# ── generate_copywriting ────────────────────────


class TestGenerateCopywriting:
    """生成文案"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.db = _make_db()

    @pytest.mark.asyncio
    async def test_brand_story(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"content": "赣南脐橙，来自大自然的馈赠"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await generate_copywriting("brand_story", "赣南脐橙", ["新鲜", "有机"], uuid.uuid4(), self.db)
        assert "content" in result
        assert "赣南脐橙" in result["content"]

    @pytest.mark.asyncio
    async def test_selling_points(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"content": {"items": ["天然", "纯正"]}}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await generate_copywriting("selling_points", "蜂蜜", ["天然", "纯正"], uuid.uuid4(), self.db)
        assert "content" in result
        assert isinstance(result["content"], dict)

    @pytest.mark.asyncio
    async def test_empty_keywords(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"content": "大米的品质之旅"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await generate_copywriting("brand_story", "大米", [], uuid.uuid4(), self.db)
        assert "content" in result


# ── suggest_page_structure ──────────────────────


class TestSuggestPageStructure:
    """页面结构建议"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.db = _make_db()

    @pytest.mark.asyncio
    async def test_fruit_category(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"modules": ["hero_banner", "origin_map", "reviews"]}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await suggest_page_structure("脐橙", "水果", uuid.uuid4(), self.db)
        modules = result["suggestion"]["modules"]
        assert "hero_banner" in modules
        assert "origin_map" in modules

    @pytest.mark.asyncio
    async def test_grain_category(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"modules": ["hero_banner", "nutrition_facts"]}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await suggest_page_structure("大米", "粮食", uuid.uuid4(), self.db)
        modules = result["suggestion"]["modules"]
        assert "nutrition_facts" in modules

    @pytest.mark.asyncio
    async def test_default_category(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"modules": ["hero_banner", "reviews"]}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await suggest_page_structure("商品", "其他", uuid.uuid4(), self.db)
        modules = result["suggestion"]["modules"]
        assert "reviews" in modules


# ── generate_page_copy ──────────────────────────


class TestGeneratePageCopy:
    """生成页面文案和推荐模板"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.db = _make_db()

    @pytest.mark.asyncio
    async def test_returns_result(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {
                "copywriting": "优质脐橙",
                "recommended_template": {"template_type": "traceability"},
                "page_suggestion": {"modules": ["hero_banner"]},
            }
            mock_save.return_value = _mock_save_generation(self.db)
            result = await generate_page_copy("赣南脐橙", "水果", ["新鲜"], uuid.uuid4(), self.db)
        assert "result" in result
        assert "generation_id" in result

    @pytest.mark.asyncio
    async def test_agriculture_category(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {
                "copywriting": "有机大米",
                "recommended_template": {"template_type": "traceability"},
                "page_suggestion": {"modules": ["nutrition_facts"]},
            }
            mock_save.return_value = _mock_save_generation(self.db)
            result = await generate_page_copy("五常大米", "粮食", ["有机"], uuid.uuid4(), self.db)
        assert result["result"]["recommended_template"]["template_type"] == "traceability"


# ── generate_campaign ───────────────────────────


class TestGenerateCampaign:
    """生成活动方案"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.db = _make_db()

    @pytest.mark.asyncio
    async def test_promotion_campaign(self):
        with (
            patch("app.services.ai._call_llm", new_callable=AsyncMock) as mock_llm,
            patch("app.services.ai._save_generation", new_callable=AsyncMock) as mock_save,
        ):
            mock_llm.return_value = {"name": "脐橙尝鲜季", "type": "promotion"}
            mock_save.return_value = _mock_save_generation(self.db)
            result = await generate_campaign("脐橙", "promotion", "年轻消费者", uuid.uuid4(), self.db)
        assert "campaign" in result
        assert result["campaign"]["name"] == "脐橙尝鲜季"
