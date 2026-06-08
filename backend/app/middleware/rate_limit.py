"""限流策略实现（Redis 优先，内存降级）"""

from dataclasses import dataclass

from app.services.redis_cache import AsyncRedisCache


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class RateLimiter:
    """码解析限流器（统一使用 AsyncRedisCache，自带 Redis + 内存降级）"""

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
        ip_key = f"resolver:ip:{ip}"
        code_key = f"resolver:code:{public_id}"

        # IP 级限流
        ip_allowed, _ = await self._cache.rate_limit_check(
            ip_key, self.ip_limit, self.window_seconds
        )
        if not ip_allowed:
            return RateLimitResult(
                allowed=False,
                retry_after=self.window_seconds,
            )

        # 码级限流
        code_allowed, _ = await self._cache.rate_limit_check(
            code_key, self.code_limit, self.window_seconds
        )
        if not code_allowed:
            return RateLimitResult(
                allowed=False,
                retry_after=self.window_seconds,
            )

        return RateLimitResult(allowed=True)


# 全局限流器实例
rate_limiter = RateLimiter()
