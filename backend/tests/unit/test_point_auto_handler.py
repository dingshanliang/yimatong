from unittest.mock import AsyncMock

import pytest

from app.services import point_auto_handler


@pytest.mark.anyio
async def test_redis_dedup_sets_five_minute_ttl(monkeypatch):
    redis = AsyncMock()
    redis.exists.return_value = False
    monkeypatch.setattr(point_auto_handler, "get_redis_pool", AsyncMock(return_value=redis))

    is_duplicate = await point_auto_handler._check_redis_dedup(
        "tenant-1",
        "consumer-1",
        "scan",
    )

    assert is_duplicate is False
    redis.setex.assert_awaited_once_with(
        "ymt:points:dedup:tenant-1:consumer-1:scan",
        300,
        "1",
    )
