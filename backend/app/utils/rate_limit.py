"""Redis 滑动窗口限流。"""

from __future__ import annotations

import time

from app.core.config import settings


async def check_rate_limit(
    identifier: str,
    limit: int,
    window_seconds: int = 60,
) -> tuple[bool, int, int]:
    """检查限流。返回 (allowed, remaining, retry_after_seconds)。

    使用 Redis sorted set 实现滑动窗口。
    """
    import redis.asyncio as aioredis

    key = f"ymt:ratelimit:{identifier}"
    now = time.time()
    window_start = now - window_seconds

    try:
        async with aioredis.from_url(settings.redis_url) as r:
            pipe = r.pipeline(transaction=True)
            # 移除过期条目
            pipe.zremrangebyscore(key, 0, window_start)
            # 添加当前请求
            pipe.zadd(key, {str(now): now})
            # 计数
            pipe.zcard(key)
            # 设置过期
            pipe.expire(key, window_seconds)
            results = await pipe.execute()

            count = results[2]
            remaining = max(0, limit - count)
            allowed = count <= limit
            retry_after = window_seconds if not allowed else 0

            return allowed, remaining, retry_after
    except Exception:
        # Redis 不可用时放行
        return True, limit, 0
