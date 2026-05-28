"""熔断器服务"""

import time
from dataclasses import dataclass


@dataclass
class CircuitBreakerState:
    """熔断器状态"""
    failure_count: int = 0
    last_failure_time: float = 0
    is_open: bool = False
    threshold: int = 5
    reset_timeout: int = 30  # 秒


class CircuitBreaker:
    """简单的熔断器实现"""

    def __init__(self, threshold: int = 5, reset_timeout: int = 30):
        self._state = CircuitBreakerState(threshold=threshold, reset_timeout=reset_timeout)

    def record_success(self):
        self._state.failure_count = 0
        self._state.is_open = False

    def record_failure(self):
        self._state.failure_count += 1
        self._state.last_failure_time = time.time()
        if self._state.failure_count >= self._state.threshold:
            self._state.is_open = True

    def is_available(self) -> bool:
        if not self._state.is_open:
            return True
        # 检查是否到了重试时间
        elapsed = time.time() - self._state.last_failure_time
        if elapsed >= self._state.reset_timeout:
            self._state.is_open = False
            self._state.failure_count = 0
            return True
        return False
