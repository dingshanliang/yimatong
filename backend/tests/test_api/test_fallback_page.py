"""A6-008: 熔断降级测试"""

from app.services.resolver import (
    NOT_ACTIVE_PAGE,
    NOT_FOUND_PAGE,
    REVOKED_PAGE,
)


class TestFallbackPages:
    def test_not_found_page_is_html(self):
        assert "<!DOCTYPE html>" in NOT_FOUND_PAGE
        assert "无效" in NOT_FOUND_PAGE

    def test_revoked_page_is_html(self):
        assert "<!DOCTYPE html>" in REVOKED_PAGE
        assert "作废" in REVOKED_PAGE

    def test_not_active_page_is_html(self):
        assert "<!DOCTYPE html>" in NOT_ACTIVE_PAGE
        assert "尚未启用" in NOT_ACTIVE_PAGE
