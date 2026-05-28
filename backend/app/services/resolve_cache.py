"""解析缓存服务（Redis + 内存降级）"""

from app.services.redis_cache import RedisCache

resolve_cache = RedisCache(prefix="resolve", default_ttl=300)
