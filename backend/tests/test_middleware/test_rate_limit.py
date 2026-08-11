"""A6-003: 限流策略测试"""

from unittest.mock import AsyncMock

import pytest

from app.middleware.rate_limit import RateLimiter
from app.services.redis_cache import SharedSecurityCacheUnavailable


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

    @pytest.mark.anyio
    async def test_resolver_uses_shared_hashed_keys(self):
        limiter = RateLimiter()
        shared = AsyncMock(return_value=(True, 1))
        limiter._cache.rate_limit_check_shared = shared

        result = await limiter.check_resolver("198.51.100.7", "PUBLIC-CODE")

        assert result.allowed is True
        assert shared.await_count == 2
        keys = [call.args[0] for call in shared.await_args_list]
        assert all("198.51.100.7" not in key and "PUBLIC-CODE" not in key for key in keys)

    @pytest.mark.anyio
    async def test_resolver_fails_closed_when_shared_cache_is_unavailable(self):
        limiter = RateLimiter()
        limiter._cache.rate_limit_check_shared = AsyncMock(
            side_effect=SharedSecurityCacheUnavailable("redis unavailable")
        )

        with pytest.raises(SharedSecurityCacheUnavailable):
            await limiter.check_resolver("198.51.100.7", "PUBLIC-CODE")
