"""AI 资料识别与文案生成服务 单元测试"""

import pytest

from app.services.ai import (
    extract_product_fields,
    extract_product_from_image,
    generate_copywriting,
    generate_page_copy,
    suggest_page_structure,
)


class TestExtractProductFields:
    """从文本提取产品字段"""

    def test_extract_product_name(self):
        result = extract_product_fields("这是一款优质的脐橙，来自江西赣州")
        assert result["fields"]["product_name"] == "脐橙"

    def test_extract_origin(self):
        result = extract_product_fields("赣南脐橙，产地江西赣州，每箱5kg")
        assert result["fields"]["origin"] == "江西赣州"

    def test_extract_weight(self):
        result = extract_product_fields("优质大米，净重5kg，保质期12个月")
        assert result["fields"]["weight"] == "5kg"

    def test_extract_shelf_life(self):
        result = extract_product_fields("蜂蜜 产地云南 保质期24个月")
        assert result["fields"]["shelf_life"] == "24月"

    def test_extract_shelf_life_without_ge(self):
        result = extract_product_fields("大米 保质期12月")
        assert result["fields"]["shelf_life"] == "12月"

    def test_unknown_product(self):
        result = extract_product_fields("这是一段没有明确产品名称的文字")
        assert "product_name" in result["fields"]

    def test_multiple_fields(self):
        text = "赣南脐橙，产地江西赣州，净重5kg，保质期6个月"
        result = extract_product_fields(text)
        assert result["fields"]["product_name"] == "脐橙"
        assert result["fields"]["origin"] == "江西赣州"
        assert result["fields"]["weight"] == "5kg"
        assert result["fields"]["shelf_life"] == "6月"


class TestExtractProductFromImage:
    """从图片识别产品信息（模拟）"""

    def test_returns_product_fields(self):
        result = extract_product_from_image(
            filename="product.jpg",
            content_type="image/jpeg",
            content=b"fake-image-bytes",
        )
        assert "fields" in result
        assert "product_name" in result["fields"]
        assert "category" in result["fields"]

    def test_png_content_type(self):
        result = extract_product_from_image(
            filename="product.png",
            content_type="image/png",
            content=b"fake-image-bytes",
        )
        assert "fields" in result

    def test_rejects_non_image(self):
        with pytest.raises(ValueError, match="not supported"):
            extract_product_from_image(
                filename="doc.pdf",
                content_type="application/pdf",
                content=b"fake-content",
            )


class TestGenerateCopywriting:
    """生成文案"""

    def test_brand_story(self):
        result = generate_copywriting("brand_story", "赣南脐橙", ["新鲜", "有机"])
        assert "content" in result
        assert "赣南脐橙" in result["content"]

    def test_selling_points(self):
        result = generate_copywriting("selling_points", "蜂蜜", ["天然", "纯正"])
        assert "content" in result
        assert isinstance(result["content"], dict)
        assert "items" in result["content"]

    def test_empty_keywords(self):
        result = generate_copywriting("brand_story", "大米", [])
        assert "content" in result


class TestGeneratePageCopy:
    """生成页面文案和推荐模板"""

    def test_returns_copy_and_template(self):
        result = generate_page_copy(
            product_name="赣南脐橙",
            category="水果",
            keywords=["新鲜", "有机"],
        )
        assert "copywriting" in result
        assert "recommended_template" in result
        assert "page_suggestion" in result
        assert result["recommended_template"]["template_type"] in [
            "traceability",
            "product_info",
            "brand_story",
        ]

    def test_agriculture_category(self):
        result = generate_page_copy(
            product_name="五常大米",
            category="粮食",
            keywords=["有机"],
        )
        assert result["recommended_template"]["template_type"] == "traceability"
        assert "page_suggestion" in result

    def test_tea_category(self):
        result = generate_page_copy(
            product_name="龙井茶",
            category="茶叶",
            keywords=["手工制作"],
        )
        assert result["recommended_template"]["template_type"] == "brand_story"

    def test_unknown_category_defaults_to_product_info(self):
        result = generate_page_copy(
            product_name="某产品",
            category="其他",
            keywords=[],
        )
        assert result["recommended_template"]["template_type"] in [
            "traceability",
            "product_info",
            "brand_story",
        ]


class TestSuggestPageStructure:
    """页面结构建议"""

    def test_fruit_category(self):
        result = suggest_page_structure("脐橙", "水果")
        modules = result["modules"]
        assert "hero_banner" in modules
        assert "origin_map" in modules

    def test_grain_category(self):
        result = suggest_page_structure("大米", "粮食")
        modules = result["modules"]
        assert "nutrition_facts" in modules

    def test_honey_category(self):
        result = suggest_page_structure("蜂蜜", "蜂蜜")
        modules = result["modules"]
        assert "craftsmanship" in modules

    def test_default_category(self):
        result = suggest_page_structure("商品", "其他")
        modules = result["modules"]
        assert "reviews" in modules
