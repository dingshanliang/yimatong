"""A5-004: 固定模板定义与 schema 校验测试"""

import pytest
from pydantic import ValidationError

from app.schemas.page import (
    BrandStoryConfig,
    ProductInfoConfig,
    TemplateType,
    TraceabilityConfig,
    validate_template_config,
)


class TestProductInfoSchema:
    def test_valid_config(self):
        config = ProductInfoConfig(
            brand_name="测试品牌",
            brand_logo="https://example.com/logo.png",
            product_name="测试产品",
            product_image="https://example.com/product.jpg",
            specifications={"weight": "500g", "origin": "中国"},
            batch_info={"batch_code": "B001", "production_date": "2026-01-01"},
        )
        assert config.brand_name == "测试品牌"
        assert config.product_name == "测试产品"

    def test_minimal_config(self):
        config = ProductInfoConfig(brand_name="品牌", product_name="产品")
        assert config.brand_logo is None
        assert config.specifications is None

    def test_missing_required_field(self):
        with pytest.raises(ValidationError) as exc_info:
            ProductInfoConfig(brand_name="品牌")
        assert "product_name" in str(exc_info.value)


class TestTraceabilitySchema:
    def test_valid_config(self):
        config = TraceabilityConfig(
            brand_name="溯源品牌",
            product_name="溯源产品",
            trace_nodes=[
                {"name": "种植", "location": "山东", "date": "2026-01-01"},
                {"name": "加工", "location": "青岛", "date": "2026-02-01"},
            ],
        )
        assert len(config.trace_nodes) == 2

    def test_minimal_config(self):
        config = TraceabilityConfig(brand_name="品牌", product_name="产品")
        assert config.trace_nodes is None


class TestBrandStorySchema:
    def test_valid_config(self):
        config = BrandStoryConfig(
            brand_name="品牌故事",
            story_title="我们的故事",
            story_content="从田间到餐桌...",
            cover_image="https://example.com/cover.jpg",
        )
        assert config.story_title == "我们的故事"

    def test_minimal_config(self):
        config = BrandStoryConfig(brand_name="品牌")
        assert config.story_content is None


class TestValidateTemplateConfig:
    def test_product_info_validation(self):
        data = {"brand_name": "品牌", "product_name": "产品"}
        result = validate_template_config("product_info", data)
        assert result["brand_name"] == "品牌"

    def test_traceability_validation(self):
        data = {"brand_name": "品牌", "product_name": "产品"}
        result = validate_template_config("traceability", data)
        assert result["brand_name"] == "品牌"

    def test_brand_story_validation(self):
        data = {"brand_name": "品牌"}
        result = validate_template_config("brand_story", data)
        assert result["brand_name"] == "品牌"

    def test_invalid_template_type(self):
        with pytest.raises(ValueError, match="Unknown template type"):
            validate_template_config("invalid_type", {})

    def test_invalid_config_data(self):
        with pytest.raises(ValidationError):
            validate_template_config("product_info", {})


class TestTemplateTypeEnum:
    def test_values(self):
        assert TemplateType.product_info == "product_info"
        assert TemplateType.traceability == "traceability"
        assert TemplateType.brand_story == "brand_story"
