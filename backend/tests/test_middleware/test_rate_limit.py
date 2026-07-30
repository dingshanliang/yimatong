"""A6-003: 限流策略测试"""

import pytest

from app.middleware.rate_limit import RateLimiter


class TestRateLimiter:
    @pytest.mark.anyio
    async def test_check_resolver_allows_normal(self):
        limiter = RateLimiter(ip_limit=10, code_limit=5, window_seconds=60)
        result = await limiter.check_resolver("192.168.1.1", "ABC123")
        assert result.allowed is True

    @pytest.mark.anyio
    async def test_check_resolver_blocks_ip(self):
        limiter = RateLimiter(ip_limit=2, code_limit=10, window_seconds=60)
        await limiter.check_resolver("192.168.1.1", "code1")
        await limiter.check_resolver("192.168.1.1", "code2")
        result = await limiter.check_resolver("192.168.1.1", "code3")
        assert result.allowed is False
        assert result.retry_after > 0

    @pytest.mark.anyio
    async def test_check_resolver_blocks_code(self):
        limiter = RateLimiter(ip_limit=100, code_limit=2, window_seconds=60)
        await limiter.check_resolver("192.168.1.1", "ABC123")
        await limiter.check_resolver("192.168.1.2", "ABC123")
        result = await limiter.check_resolver("192.168.1.3", "ABC123")
        assert result.allowed is False
