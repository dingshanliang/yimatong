from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import consumer_admission
from app.services.redis_cache import SharedSecurityCacheUnavailable


@pytest.mark.anyio
async def test_public_consumer_admission_hashes_ip_and_scan_subject(monkeypatch):
    check = AsyncMock(side_effect=[(True, 29), (True, 9)])
    monkeypatch.setattr(consumer_admission._security_cache, "rate_limit_check_shared", check)

    await consumer_admission.enforce_public_consumer_admission("203.0.113.9", "scan-jti-1")

    keys = [call.args[0] for call in check.await_args_list]
    assert len(keys) == 2
    assert all("203.0.113.9" not in key and "scan-jti-1" not in key for key in keys)
    assert check.await_args_list[0].args[1:] == (30, 60)
    assert check.await_args_list[1].args[1:] == (10, 60)


@pytest.mark.anyio
async def test_public_consumer_admission_is_fail_closed(monkeypatch):
    monkeypatch.setattr(
        consumer_admission._security_cache,
        "rate_limit_check_shared",
        AsyncMock(side_effect=SharedSecurityCacheUnavailable("down")),
    )

    with pytest.raises(HTTPException) as raised:
        await consumer_admission.enforce_public_consumer_admission("203.0.113.9", "scan-jti-1")

    assert raised.value.status_code == 503


@pytest.mark.anyio
async def test_public_consumer_admission_returns_retry_after_for_either_limit(monkeypatch):
    check = AsyncMock(side_effect=[(True, 29), (False, 0)])
    monkeypatch.setattr(consumer_admission._security_cache, "rate_limit_check_shared", check)

    with pytest.raises(HTTPException) as raised:
        await consumer_admission.enforce_public_consumer_admission("203.0.113.9", "scan-jti-1")

    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "60"}
