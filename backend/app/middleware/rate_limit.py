"""限流策略实现；公开解析使用共享 Redis fail-closed admission。"""

import hashlib
import hmac
from dataclasses import dataclass

from app.core.config import settings
from app.services.redis_cache import AsyncRedisCache


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class RateLimiter:
    """Public resolver admission backed by the shared atomic Redis boundary."""

    def __init__(
        self,
        ip_limit: int = 100,
        code_limit: int = 10,
        window_seconds: int = 60,
    ):
        self.ip_limit = ip_limit
        self.code_limit = code_limit
        self.window_seconds = window_seconds
        self._cache = AsyncRedisCache(prefix="rate_limit", default_ttl=window_seconds)

    async def check_resolver(self, ip: str, public_id: str) -> RateLimitResult:
        key = (settings.hmac_pepper or settings.secret_key).encode()
        ip_digest = hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()
        code_digest = hmac.new(key, public_id.encode(), hashlib.sha256).hexdigest()
        ip_key = f"resolver:ip:{ip_digest}"
        code_key = f"resolver:code:{code_digest}"

        # IP 级限流
        ip_allowed, _ = await self._cache.rate_limit_check_shared(ip_key, self.ip_limit, self.window_seconds)
        if not ip_allowed:
            return RateLimitResult(
                allowed=False,
                retry_after=self.window_seconds,
            )

        # 码级限流
        code_allowed, _ = await self._cache.rate_limit_check_shared(code_key, self.code_limit, self.window_seconds)
        if not code_allowed:
            return RateLimitResult(
                allowed=False,
                retry_after=self.window_seconds,
            )

        return RateLimitResult(allowed=True)

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        """通用限流检查（用于非 resolver 场景，如 claim 等）"""
        allowed, _ = await self._cache.rate_limit_check(key, limit, window)
        if not allowed:
            return RateLimitResult(allowed=False, retry_after=window)
        return RateLimitResult(allowed=True)


# 全局限流器实例
rate_limiter = RateLimiter()
