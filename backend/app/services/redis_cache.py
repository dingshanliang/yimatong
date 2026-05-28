"""Redis 缓存服务（带内存降级）"""

import json
import logging
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception:
        logger.warning("Redis unavailable, using in-memory fallback")
        _redis_client = None
        return None


class RedisCache:
    """Redis 缓存，自动降级到内存"""

    def __init__(self, prefix: str = "ymt", default_ttl: int = 300):
        self.prefix = prefix
        self.default_ttl = default_ttl
        self._mem_store: dict[str, tuple[str, float]] = {}

    def _key(self, k: str) -> str:
        return f"{self.prefix}:{k}"

    def get(self, key: str) -> dict | None:
        full_key = self._key(key)
        r = _get_redis()
        if r:
            raw = r.get(full_key)
            if raw:
                return json.loads(raw)
            return None
        # 内存降级
        entry = self._mem_store.get(full_key)
        if not entry:
            return None
        val, expire_at = entry
        if time.time() > expire_at:
            del self._mem_store[full_key]
            return None
        return json.loads(val)

    def set(self, key: str, value: dict, ttl: int | None = None) -> None:
        full_key = self._key(key)
        ttl = ttl or self.default_ttl
        serialized = json.dumps(value)
        r = _get_redis()
        if r:
            r.setex(full_key, ttl, serialized)
            return
        # 内存降级
        self._mem_store[full_key] = (serialized, time.time() + ttl)

    def invalidate(self, key: str) -> None:
        full_key = self._key(key)
        r = _get_redis()
        if r:
            r.delete(full_key)
            return
        self._mem_store.pop(full_key, None)

    def set_idempotent(self, key: str, ttl: int = 60) -> bool:
        """设置幂等键，返回 True 表示首次设置，False 表示已存在"""
        full_key = self._key(f"idem:{key}")
        r = _get_redis()
        if r:
            return r.set(full_key, "1", nx=True, ex=ttl) is not None
        # 内存降级
        now = time.time()
        entry = self._mem_store.get(full_key)
        if entry and now < entry[1]:
            return False
        self._mem_store[full_key] = ("1", now + ttl)
        return True
