"""Redis 缓存服务（异步 + 内存降级）"""

from __future__ import annotations

import json
import logging
import time
from asyncio import Lock
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    import redis.asyncio

logger = logging.getLogger(__name__)

_redis_pool: redis.asyncio.Redis | None = None


class SharedSecurityCacheUnavailable(RuntimeError):
    """安全凭证不能降级到单进程内存时抛出。"""


async def get_redis_pool() -> redis.asyncio.Redis | None:
    """获取或创建异步 Redis 连接池（单例）"""
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool
    try:
        import redis.asyncio

        _redis_pool = redis.asyncio.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
        await _redis_pool.ping()
        return _redis_pool
    except Exception as e:
        logger.warning("Redis unavailable (%s), using in-memory fallback", e)
        _redis_pool = None
        return None


async def close_redis_pool() -> None:
    """关闭 Redis 连接池（应用关闭时调用）"""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None


class AsyncRedisCache:
    """异步 Redis 缓存，自动降级到内存"""

    _mem_lock = Lock()
    _mem_store: dict[str, tuple[str, float]] = {}

    def __init__(self, prefix: str = "ymt", default_ttl: int = 300):
        self.prefix = prefix
        self.default_ttl = default_ttl

    def _key(self, k: str) -> str:
        return f"{self.prefix}:{k}"

    async def get(self, key: str) -> dict | None:
        full_key = self._key(key)
        r = await get_redis_pool()
        if r:
            try:
                raw = await r.get(full_key)
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

    async def set(self, key: str, value: dict, ttl: int | None = None) -> None:
        full_key = self._key(key)
        ttl = ttl or self.default_ttl
        serialized = json.dumps(value)
        r = await get_redis_pool()
        if r:
            try:
                await r.setex(full_key, ttl, serialized)
                return
            except Exception:
                pass
        self._mem_store[full_key] = (serialized, time.time() + ttl)

    async def set_shared(self, key: str, value: dict, ttl: int) -> None:
        """安全凭证必须写入所有 worker 共享的 Redis，禁止内存降级。"""
        r = await get_redis_pool()
        if r is None:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用")
        try:
            await r.setex(self._key(key), ttl, json.dumps(value))
        except Exception as exc:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用") from exc

    async def set_shared_if_absent(self, key: str, value: dict, ttl: int) -> bool:
        """Restore a consumed credential only when no newer credential exists."""
        r = await get_redis_pool()
        if r is None:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用")
        try:
            return bool(await r.set(self._key(key), json.dumps(value), nx=True, ex=ttl))
        except Exception as exc:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用") from exc

    async def get_shared(self, key: str) -> dict | None:
        r = await get_redis_pool()
        if r is None:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用")
        try:
            raw = await r.get(self._key(key))
            return json.loads(raw) if raw else None
        except Exception as exc:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用") from exc

    async def invalidate(self, key: str) -> None:
        full_key = self._key(key)
        r = await get_redis_pool()
        if r:
            try:
                await r.delete(full_key)
                return
            except Exception:
                pass
        self._mem_store.pop(full_key, None)

    async def consume(self, key: str, expected: dict) -> bool:
        """Atomically delete a JSON value only when it still matches expected."""
        full_key = self._key(key)
        serialized = json.dumps(expected)
        r = await get_redis_pool()
        if r:
            try:
                result = await r.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    full_key,
                    serialized,
                )
                return bool(result)
            except Exception:
                return False
        async with self._mem_lock:
            entry = self._mem_store.get(full_key)
            if entry is None or time.time() > entry[1] or entry[0] != serialized:
                return False
            del self._mem_store[full_key]
            return True

    async def consume_shared(self, key: str, expected: dict) -> bool:
        full_key = self._key(key)
        serialized = json.dumps(expected)
        r = await get_redis_pool()
        if r is None:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用")
        try:
            result = await r.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                full_key,
                serialized,
            )
            return bool(result)
        except Exception as exc:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用") from exc

    async def revoke_token(self, jti: str, ttl: int) -> None:
        await self.set_shared(f"revoked:{jti}", {"revoked": True}, ttl=ttl)

    async def is_token_revoked(self, jti: str) -> bool:
        return await self.get_shared(f"revoked:{jti}") is not None

    async def rate_limit_check_shared(self, key: str, max_attempts: int, window_seconds: int) -> tuple[bool, int]:
        """Atomic shared rate limit for authentication boundaries.

        Security-sensitive callers must not fall back to per-process memory,
        otherwise each worker would enforce an independent limit.
        """
        r = await get_redis_pool()
        if r is None:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用")
        try:
            count = int(
                await r.eval(
                    """
                    local current = redis.call('INCR', KEYS[1])
                    if current == 1 then
                        redis.call('EXPIRE', KEYS[1], ARGV[1])
                    end
                    return current
                    """,
                    1,
                    self._key(f"rl:{key}"),
                    window_seconds,
                )
            )
        except Exception as exc:
            raise SharedSecurityCacheUnavailable("共享安全缓存不可用") from exc
        remaining = max(0, max_attempts - count)
        return count <= max_attempts, remaining

    async def rate_limit_check(self, key: str, max_attempts: int, window_seconds: int) -> tuple[bool, int]:
        """滑动窗口速率限制。返回 (allowed, remaining_attempts)。"""
        now = time.time()
        window_start = now - window_seconds

        r = await get_redis_pool()
        if r:
            try:
                pipe = r.pipeline()
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                pipe.zadd(key, {str(now): now})
                pipe.expire(key, window_seconds)
                results = await pipe.execute()
                current_count = results[1]
                remaining = max(0, max_attempts - current_count - 1)
                allowed = current_count < max_attempts
                return allowed, remaining
            except Exception:
                pass  # Fall through to in-memory

        # In-memory fallback
        mem_key = self._key(f"rl:{key}")
        entry = self._mem_store.get(mem_key)
        if entry:
            val, expire_at = entry
            if time.time() > expire_at:
                del self._mem_store[mem_key]
                entry = None

        if entry:
            count = int(json.loads(entry[0]).get("count", 0))
        else:
            count = 0

        allowed = count < max_attempts
        new_count = count + 1
        self._mem_store[mem_key] = (
            json.dumps({"count": new_count}),
            time.time() + window_seconds,
        )
        remaining = max(0, max_attempts - new_count)
        return allowed, remaining

    async def set_idempotent(self, key: str, ttl: int = 60) -> bool:
        """设置幂等键，返回 True 表示首次设置，False 表示已存在"""
        full_key = self._key(f"idem:{key}")
        r = await get_redis_pool()
        if r:
            try:
                return await r.set(full_key, "1", nx=True, ex=ttl) is not None
            except Exception:
                pass
        now = time.time()
        entry = self._mem_store.get(full_key)
        if entry and now < entry[1]:
            return False
        self._mem_store[full_key] = ("1", now + ttl)
        return True


# ---------------------------------------------------------------------------
# Deprecated: sync RedisCache — kept for reference during migration.
# All new code should use AsyncRedisCache.
# ---------------------------------------------------------------------------


class RedisCache:  # noqa: SIM119 — dataclass-style class kept for compat
    """同步 Redis 缓存（已废弃，请使用 AsyncRedisCache）"""

    def __init__(self, prefix: str = "ymt", default_ttl: int = 300):
        self.prefix = prefix
        self.default_ttl = default_ttl
        self._mem_store: dict[str, tuple[str, float]] = {}

    def _key(self, k: str) -> str:
        return f"{self.prefix}:{k}"

    def get(self, key: str) -> dict | None:
        full_key = self._key(key)
        r = _get_sync_redis()
        if r:
            try:
                raw = r.get(full_key)
                if raw:
                    return json.loads(raw)
                return None
            except Exception:
                return None
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
        r = _get_sync_redis()
        if r:
            try:
                r.setex(full_key, ttl, serialized)
                return
            except Exception:
                pass
        self._mem_store[full_key] = (serialized, time.time() + ttl)

    def invalidate(self, key: str) -> None:
        full_key = self._key(key)
        r = _get_sync_redis()
        if r:
            try:
                r.delete(full_key)
                return
            except Exception:
                pass
        self._mem_store.pop(full_key, None)

    def revoke_token(self, jti: str, ttl: int) -> None:
        self.set(f"revoked:{jti}", {"revoked": True}, ttl=ttl)

    def is_token_revoked(self, jti: str) -> bool:
        return self.get(f"revoked:{jti}") is not None

    def set_idempotent(self, key: str, ttl: int = 60) -> bool:
        full_key = self._key(f"idem:{key}")
        r = _get_sync_redis()
        if r:
            try:
                return r.set(full_key, "1", nx=True, ex=ttl) is not None
            except Exception:
                pass
        now = time.time()
        entry = self._mem_store.get(full_key)
        if entry and now < entry[1]:
            return False
        self._mem_store[full_key] = ("1", now + ttl)
        return True


_sync_redis_client = None
_sync_redis_failed_at: float = 0
_RETRY_INTERVAL = 30


def _get_sync_redis():
    global _sync_redis_client, _sync_redis_failed_at
    if _sync_redis_client is not None:
        return _sync_redis_client
    if _sync_redis_failed_at and time.time() - _sync_redis_failed_at < _RETRY_INTERVAL:
        return None
    try:
        import redis

        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _sync_redis_client = client
        _sync_redis_failed_at = 0
        return _sync_redis_client
    except Exception as e:
        logger.warning("Redis unavailable (%s), using in-memory fallback", e)
        _sync_redis_failed_at = time.time()
        return None
