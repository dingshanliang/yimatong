"""LLM API Key 池 — 轮询分配 + 429 冷却

支持单个或多个 API key，自动轮询分配。
当某个 key 触发 429 限流时，标记冷却期，跳过该 key。
"""

import logging
import time

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMKeyPool:
    def __init__(self, keys: list[str], base_url: str):
        if not keys:
            raise ValueError("至少需要一个 API key")
        self._clients: list[tuple[AsyncOpenAI, float]] = [
            (AsyncOpenAI(api_key=k, base_url=base_url), 0.0) for k in keys
        ]
        self._idx = 0
        logger.info("LLM Key Pool 初始化完成，共 %d 个 key", len(self._clients))

    def get_client(self) -> AsyncOpenAI:
        """获取一个可用的 client，跳过冷却中的 key"""
        now = time.monotonic()
        n = len(self._clients)
        for _ in range(n):
            client, cooldown_until = self._clients[self._idx]
            if now >= cooldown_until:
                return client
            self._idx = (self._idx + 1) % n
        # 所有 key 都在冷却，返回第一个（降级策略）
        logger.warning("所有 API key 均在冷却期，使用第一个 key 降级请求")
        return self._clients[0][0]

    def mark_rate_limited(self, cooldown_seconds: float = 5.0) -> None:
        """标记当前 key 进入冷却期"""
        client, _ = self._clients[self._idx]
        until = time.monotonic() + cooldown_seconds
        self._clients[self._idx] = (client, until)
        logger.info("API key [%d] 触发限流，冷却 %.1f 秒", self._idx, cooldown_seconds)

    @property
    def client_count(self) -> int:
        return len(self._clients)


def _parse_keys() -> list[str]:
    raw = settings.deepseek_api_keys.strip()
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def create_pool() -> LLMKeyPool | None:
    """根据配置创建 key pool，未配置 key 时返回 None"""
    keys = _parse_keys()
    if not keys:
        return None
    return LLMKeyPool(keys=keys, base_url=settings.deepseek_base_url)


# 全局单例，应用启动时初始化
pool: LLMKeyPool | None = None


def init_pool() -> None:
    global pool
    pool = create_pool()


def get_pool() -> LLMKeyPool:
    if pool is None:
        raise RuntimeError("LLM Key Pool 未初始化，请检查 DEEPSEEK_API_KEYS 配置")
    return pool
