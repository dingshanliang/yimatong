"""A6-008: 熔断器测试"""

import time

from app.services.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_initially_available(self):
        cb = CircuitBreaker(threshold=3)
        assert cb.is_available() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_available() is False

    def test_success_resets(self):
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_available() is True

    def test_resets_after_timeout(self):
        cb = CircuitBreaker(threshold=2, reset_timeout=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_available() is False
        time.sleep(1.1)
        assert cb.is_available() is True
