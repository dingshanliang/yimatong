"""解析缓存服务（内存实现，可替换为 Redis）"""

import time
from dataclasses import dataclass


@dataclass
class CacheEntry:
    value: dict
    expire_at: float


class ResolveCache:
    """码解析和页面版本缓存"""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> dict | None:
        entry = self._store.get(key)
        if not entry:
            return None
        if time.time() > entry.expire_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: dict) -> None:
        self._store[key] = CacheEntry(
            value=value,
            expire_at=time.time() + self.ttl_seconds,
        )

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


# 全局缓存实例
resolve_cache = ResolveCache(ttl_seconds=300)
