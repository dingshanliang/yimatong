"""限流策略实现"""

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class SlidingWindowCounter:
    """滑动窗口计数器（内存实现，可替换为 Redis）"""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds

        # 清理过期记录
        self._windows[key] = [t for t in self._windows[key] if t > cutoff]

        if len(self._windows[key]) >= self.max_requests:
            return False

        self._windows[key].append(now)
        return True

    def get_retry_after(self, key: str) -> int:
        now = time.time()
        cutoff = now - self.window_seconds
        records = [t for t in self._windows[key] if t > cutoff]
        if records:
            oldest = min(records)
            return int(oldest + self.window_seconds - now) + 1
        return 0


class RateLimiter:
    """码解析限流器"""

    def __init__(
        self,
        ip_limit: int = 100,
        code_limit: int = 10,
        window_seconds: int = 60,
    ):
        self.ip_limiter = SlidingWindowCounter(ip_limit, window_seconds)
        self.code_limiter = SlidingWindowCounter(code_limit, window_seconds)

    def check_ip(self, ip: str) -> bool:
        return self.ip_limiter.check(f"ip:{ip}")

    def check_code(self, key: str) -> bool:
        return self.code_limiter.check(f"code:{key}")

    def check_resolver(self, ip: str, public_id: str) -> RateLimitResult:
        if not self.check_ip(ip):
            return RateLimitResult(
                allowed=False,
                retry_after=self.ip_limiter.get_retry_after(f"ip:{ip}"),
            )

        if not self.check_code(public_id):
            return RateLimitResult(
                allowed=False,
                retry_after=self.code_limiter.get_retry_after(f"code:{public_id}"),
            )

        return RateLimitResult(allowed=True)


# 全局限流器实例
rate_limiter = RateLimiter()
