"""A5-004: 页面 DSL Schema 校验测试（对齐前端模块化 DSL）"""

import pytest
from pydantic import ValidationError

from app.schemas.page_dsl import (
    ModuleType,
    PageDSLSchema,
    validate_page_dsl,
)


class TestPageDSLSchema:
    def test_valid_dsl_with_modules(self):
        dsl = {
            "modules": [
                {"id": "hero", "type": "product_hero", "enabled": True, "config": {"show_verify_badge": True}},
                {"id": "trace", "type": "light_traceability", "enabled": True, "config": {"fields": ["origin"]}},
            ],
            "routing": {"default_page": True, "campaign_periods": [{"mode": "evergreen"}]},
        }
        result = validate_page_dsl(dsl)
        assert len(result["modules"]) == 2
        assert result["modules"][0]["type"] == "product_hero"

    def test_empty_dsl(self):
        result = validate_page_dsl({})
        assert result["modules"] == []

    def test_module_missing_id(self):
        dsl = {"modules": [{"type": "product_hero"}]}
        with pytest.raises(ValidationError):
            validate_page_dsl(dsl)

    def test_module_missing_type(self):
        dsl = {"modules": [{"id": "hero"}]}
        with pytest.raises(ValidationError):
            validate_page_dsl(dsl)

    def test_invalid_module_type(self):
        dsl = {"modules": [{"id": "x", "type": "nonexistent_type"}]}
        with pytest.raises(ValidationError):
            validate_page_dsl(dsl)

    def test_all_module_types_valid(self):
        """确保所有 20 种模块类型都能通过校验"""
        for mt in ModuleType:
            dsl = {"modules": [{"id": f"test_{mt.value}", "type": mt.value}]}
            result = validate_page_dsl(dsl)
            assert result["modules"][0]["type"] == mt.value

    def test_routing_campaign_periods(self):
        dsl = {
            "routing": {
                "default_page": False,
                "campaign_periods": [
                    {"mode": "campaign", "start_at": "2026-01-01", "end_at": "2026-12-31"},
                ],
            }
        }
        result = validate_page_dsl(dsl)
        assert result["routing"]["campaign_periods"][0]["mode"] == "campaign"

    def test_routing_invalid_mode(self):
        dsl = {"routing": {"campaign_periods": [{"mode": "invalid"}]}}
        with pytest.raises(ValidationError):
            validate_page_dsl(dsl)

    def test_extra_fields_allowed(self):
        """DSL 允许额外字段（向后兼容）"""
        dsl = {"modules": [], "dsl_version": "1.0", "theme": {"color": "#fff"}}
        result = validate_page_dsl(dsl)
        assert result["dsl_version"] == "1.0"


class TestModuleTypeEnum:
    def test_values(self):
        assert ModuleType.product_hero.value == "product_hero"
        assert ModuleType.light_traceability.value == "light_traceability"
        assert ModuleType.custom_html.value == "custom_html"

    def test_count(self):
        assert len(ModuleType) == 20
