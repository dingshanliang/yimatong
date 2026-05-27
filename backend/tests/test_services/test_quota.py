"""A1-009: 套餐额度字段与限制检查验收测试"""

from app.services.quota import QuotaExceededError, check_quota


class TestQuota:
    def test_quota_check_passes_within_limit(self):
        quota = {"max_codes": 1000, "max_campaigns": 10, "max_accounts": 5}
        check_quota(quota, "max_codes", 500)

    def test_quota_check_fails_at_limit(self):
        quota = {"max_codes": 1000, "max_campaigns": 10, "max_accounts": 5}
        import pytest

        with pytest.raises(QuotaExceededError):
            check_quota(quota, "max_codes", 1001)

    def test_quota_check_exact_limit_passes(self):
        quota = {"max_codes": 1000}
        check_quota(quota, "max_codes", 1000)

    def test_quota_exceeded_is_exception(self):
        assert issubclass(QuotaExceededError, Exception)
