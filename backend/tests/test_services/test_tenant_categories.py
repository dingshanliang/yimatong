"""Tests for tenant categories: industry defaults and schema validation."""

import pytest
from pydantic import ValidationError

from app.constants.categories import DEFAULT_CATEGORIES, INDUSTRY_DEFAULT_CATEGORIES, get_default_categories
from app.schemas.tenant import TenantUpdate


class TestGetDefaultCategories:
    def test_returns_industry_specific_categories(self):
        result = get_default_categories("茶叶")
        assert result == INDUSTRY_DEFAULT_CATEGORIES["茶叶"]

    def test_returns_copy_not_reference(self):
        result = get_default_categories("茶叶")
        result.append("新茶")
        assert "新茶" not in INDUSTRY_DEFAULT_CATEGORIES["茶叶"]

    def test_returns_default_for_unknown_industry(self):
        result = get_default_categories("未知行业")
        assert result == DEFAULT_CATEGORIES

    def test_returns_default_for_none_industry(self):
        result = get_default_categories(None)
        assert result == DEFAULT_CATEGORIES


class TestCategoryValidation:
    def test_accepts_valid_categories(self):
        schema = TenantUpdate(categories=["大米", "面粉", "食用油"])
        assert schema.categories == ["大米", "面粉", "食用油"]

    def test_trims_whitespace(self):
        schema = TenantUpdate(categories=["  大米  ", "面粉"])
        assert schema.categories == ["大米", "面粉"]

    def test_deduplicates_case_insensitive(self):
        schema = TenantUpdate(categories=["大米", "大米", "DaMi"])
        assert schema.categories == ["大米", "DaMi"]

    def test_deduplicates_ascii_case_insensitive(self):
        schema = TenantUpdate(categories=["abc", "ABC", "Abc"])
        assert schema.categories == ["abc"]

    def test_removes_empty_strings(self):
        schema = TenantUpdate(categories=["大米", "", "  ", "面粉"])
        assert schema.categories == ["大米", "面粉"]

    def test_rejects_too_long_category(self):
        with pytest.raises(ValidationError, match="不能超过 20 个字符"):
            TenantUpdate(categories=["A" * 21])

    def test_rejects_too_many_categories(self):
        with pytest.raises(ValidationError, match="不能超过 100 条"):
            TenantUpdate(categories=[f"品类{i}" for i in range(101)])

    def test_accepts_none(self):
        schema = TenantUpdate(categories=None)
        assert schema.categories is None

    def test_accepts_empty_list(self):
        schema = TenantUpdate(categories=[])
        assert schema.categories == []
