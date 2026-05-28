"""A6-003: 限流策略测试"""

import time

from app.middleware.rate_limit import RateLimiter, SlidingWindowCounter


class TestSlidingWindowCounter:
    def test_allows_within_limit(self):
        counter = SlidingWindowCounter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert counter.check("key1") is True

    def test_blocks_over_limit(self):
        counter = SlidingWindowCounter(max_requests=3, window_seconds=60)
        for _ in range(3):
            counter.check("key1")
        assert counter.check("key1") is False

    def test_different_keys_independent(self):
        counter = SlidingWindowCounter(max_requests=2, window_seconds=60)
        assert counter.check("key1") is True
        assert counter.check("key1") is True
        assert counter.check("key1") is False
        # key2 不受影响
        assert counter.check("key2") is True

    def test_window_expiry(self):
        counter = SlidingWindowCounter(max_requests=2, window_seconds=1)
        counter.check("key1")
        counter.check("key1")
        assert counter.check("key1") is False
        # 等待窗口过期
        time.sleep(1.1)
        assert counter.check("key1") is True


class TestRateLimiter:
    def test_ip_rate_limit(self):
        limiter = RateLimiter(ip_limit=5, code_limit=3, window_seconds=60)
        for _ in range(5):
            assert limiter.check_ip("192.168.1.1") is True
        assert limiter.check_ip("192.168.1.1") is False

    def test_code_rate_limit(self):
        limiter = RateLimiter(ip_limit=100, code_limit=3, window_seconds=60)
        key = "192.168.1.1:ABC123"
        for _ in range(3):
            assert limiter.check_code(key) is True
        assert limiter.check_code(key) is False

    def test_check_resolver_allows_normal(self):
        limiter = RateLimiter(ip_limit=10, code_limit=5, window_seconds=60)
        result = limiter.check_resolver("192.168.1.1", "ABC123")
        assert result.allowed is True

    def test_check_resolver_blocks_ip(self):
        limiter = RateLimiter(ip_limit=2, code_limit=10, window_seconds=60)
        limiter.check_resolver("192.168.1.1", "code1")
        limiter.check_resolver("192.168.1.1", "code2")
        result = limiter.check_resolver("192.168.1.1", "code3")
        assert result.allowed is False
        assert result.retry_after > 0

    def test_check_resolver_blocks_code(self):
        limiter = RateLimiter(ip_limit=100, code_limit=2, window_seconds=60)
        limiter.check_resolver("192.168.1.1", "ABC123")
        limiter.check_resolver("192.168.1.2", "ABC123")
        result = limiter.check_resolver("192.168.1.3", "ABC123")
        assert result.allowed is False
