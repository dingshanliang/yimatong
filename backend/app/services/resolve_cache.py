"""解析缓存服务（异步 Redis + 内存降级）"""

from app.services.redis_cache import AsyncRedisCache

resolve_cache = AsyncRedisCache(prefix="resolve", default_ttl=300)
