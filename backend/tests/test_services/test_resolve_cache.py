"""A6-004: 缓存策略测试"""

import uuid

from app.services.redis_cache import RedisCache


class TestResolveCache:
    def test_cache_set_and_get(self):
        cache = RedisCache(prefix="test", default_ttl=300)
        cache.set("resolve:ABC123", {"public_id": "ABC123", "status": "activated"})
        result = cache.get("resolve:ABC123")
        assert result is not None
        assert result["public_id"] == "ABC123"

    def test_cache_miss(self):
        cache = RedisCache(prefix="test", default_ttl=300)
        result = cache.get("nonexistent")
        assert result is None

    def test_cache_invalidate(self):
        cache = RedisCache(prefix="test", default_ttl=300)
        cache.set("resolve:ABC123", {"data": True})
        cache.invalidate("resolve:ABC123")
        assert cache.get("resolve:ABC123") is None

    def test_cache_overwrite(self):
        cache = RedisCache(prefix="test", default_ttl=300)
        cache.set("key1", {"version": 1})
        cache.set("key1", {"version": 2})
        assert cache.get("key1")["version"] == 2

    def test_page_version_cache_key(self):
        """页面版本缓存 key 格式验证"""
        template_id = uuid.uuid4()
        key = f"page_version:{template_id}"
        cache = RedisCache(prefix="test", default_ttl=300)
        cache.set(key, {"html": "<html></html>"})
        assert cache.get(key) is not None

    def test_idempotent_key(self):
        """幂等键测试"""
        import uuid as _uuid
        cache = RedisCache(prefix=f"test-{_uuid.uuid4().hex[:8]}", default_ttl=60)
        assert cache.set_idempotent("claim:123") is True
        assert cache.set_idempotent("claim:123") is False
        assert cache.set_idempotent("claim:456") is True
