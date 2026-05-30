"""Redis 缓存服务（带内存降级）"""

import json
import logging
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client = None
_redis_failed_at: float = 0
_RETRY_INTERVAL = 30  # seconds between reconnection attempts


def _get_redis():
    global _redis_client, _redis_failed_at
    if _redis_client is not None:
        return _redis_client
    # Throttle reconnection attempts
    if _redis_failed_at and time.time() - _redis_failed_at < _RETRY_INTERVAL:
        return None
    try:
        import redis

        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis_client = client
        _redis_failed_at = 0
        return _redis_client
    except Exception as e:
        logger.warning("Redis unavailable (%s), using in-memory fallback", e)
        _redis_failed_at = time.time()
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
            try:
                raw = r.get(full_key)
                if raw:
                    return json.loads(raw)
                return None
            except Exception:
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
            try:
                r.setex(full_key, ttl, serialized)
                return
            except Exception:
                pass
        # 内存降级
        self._mem_store[full_key] = (serialized, time.time() + ttl)

    def invalidate(self, key: str) -> None:
        full_key = self._key(key)
        r = _get_redis()
        if r:
            try:
                r.delete(full_key)
                return
            except Exception:
                pass
        self._mem_store.pop(full_key, None)

    def revoke_token(self, jti: str, ttl: int) -> None:
        """Add a JWT jti to the revocation list."""
        self.set(f"revoked:{jti}", {"revoked": True}, ttl=ttl)

    def is_token_revoked(self, jti: str) -> bool:
        """Check if a JWT jti has been revoked."""
        return self.get(f"revoked:{jti}") is not None

    def set_idempotent(self, key: str, ttl: int = 60) -> bool:
        """设置幂等键，返回 True 表示首次设置，False 表示已存在"""
        full_key = self._key(f"idem:{key}")
        r = _get_redis()
        if r:
            try:
                return r.set(full_key, "1", nx=True, ex=ttl) is not None
            except Exception:
                pass
        # 内存降级
        now = time.time()
        entry = self._mem_store.get(full_key)
        if entry and now < entry[1]:
            return False
        self._mem_store[full_key] = ("1", now + ttl)
        return True
