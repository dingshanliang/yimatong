"""Security contract tests for public scan telemetry."""

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v1 import scan_events
from app.api.v1.scan_events import ScanEventRequest
from app.services.redis_cache import SharedSecurityCacheUnavailable
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash


def _request(token: str, client_ip: str = "198.51.100.42") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/scan-events",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": (client_ip, 443),
        }
    )


def _body(**overrides) -> ScanEventRequest:
    values = {
        "event_type": "view",
        "public_id": "valid-public-id",
        "client_event_id": "telemetry-event-1",
    }
    values.update(overrides)
    return ScanEventRequest(**values)


@pytest.mark.parametrize(
    "payload",
    [
        {"event_type": "arbitrary", "public_id": "pid", "client_event_id": "event-1"},
        {"event_type": "view", "public_id": "pid", "client_event_id": "event-1", "action": "free-form"},
        {"event_type": "view", "public_id": "pid", "client_event_id": "event-1", "value": {"nested": True}},
        {"event_type": "view", "public_id": "pid"},
        {"event_type": "view", "public_id": "pid", "client_event_id": "x" * 101},
        {"event_type": "view", "public_id": "pid", "client_event_id": "event with spaces"},
        {"event_type": "view", "public_id": "pid", "client_event_id": "event-1", "timestamp": "not-a-date"},
    ],
)
def test_scan_event_request_rejects_unbounded_or_non_idempotent_payloads(payload):
    with pytest.raises(ValidationError):
        ScanEventRequest(**payload)


@pytest.mark.anyio
async def test_shared_rate_limit_unavailable_returns_503(monkeypatch):
    limiter = AsyncMock(side_effect=SharedSecurityCacheUnavailable("down"))
    monkeypatch.setattr(scan_events._security_cache, "rate_limit_check_shared", limiter)

    with pytest.raises(HTTPException) as exc_info:
        await scan_events.report_scan_event(_request("invalid-token"), _body(), AsyncMock())

    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_ip_rate_limit_returns_429_without_raw_ip_in_key(monkeypatch):
    limiter = AsyncMock(return_value=(False, 0))
    monkeypatch.setattr(scan_events._security_cache, "rate_limit_check_shared", limiter)
    client_ip = "198.51.100.43"

    response = await scan_events.report_scan_event(_request("invalid-token", client_ip), _body(), AsyncMock())

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert json.loads(response.body)["detail"]
    assert client_ip not in limiter.await_args.args[0]


@pytest.mark.anyio
async def test_token_tenant_rate_limit_is_shared_hashed_and_precedes_database(monkeypatch):
    limiter = AsyncMock(side_effect=[(True, 119), (False, 0)])
    monkeypatch.setattr(scan_events._security_cache, "rate_limit_check_shared", limiter)
    client_ip = "198.51.100.44"
    tenant_id = str(uuid.uuid4())
    public_id = "valid-public-id"
    token = create_scan_token(public_id, compute_ip_hash(client_ip), tenant_id=tenant_id)
    db = AsyncMock()

    response = await scan_events.report_scan_event(
        _request(token, client_ip),
        _body(public_id=public_id),
        db,
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert db.execute.await_count == 0
    second_key = limiter.await_args_list[1].args[0]
    assert token not in second_key
    assert tenant_id not in second_key


@pytest.mark.anyio
async def test_persistence_failure_returns_retryable_503_and_rolls_back(monkeypatch):
    tenant_id = uuid.uuid4()
    public_id = "valid-public-id"
    client_ip = "198.51.100.45"
    token = create_scan_token(public_id, compute_ip_hash(client_ip), tenant_id=str(tenant_id))
    db = AsyncMock()

    monkeypatch.setattr(
        scan_events._security_cache,
        "rate_limit_check_shared",
        AsyncMock(return_value=(True, 119)),
    )
    monkeypatch.setattr(scan_events, "lock_active_tenant_context", AsyncMock(return_value=tenant_id))
    monkeypatch.setattr(
        scan_events,
        "insert_intent_event_idempotent",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await scan_events.report_scan_event(
            _request(token, client_ip),
            _body(public_id=public_id),
            db,
        )

    assert exc_info.value.status_code == 503
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
