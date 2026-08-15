import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_with_bypass
from app.main import app
from app.utils.security import create_access_token


@pytest.fixture
async def client(db, monkeypatch):
    async def override_db():
        yield db

    app.dependency_overrides[get_db_with_bypass] = override_db
    monkeypatch.setattr("app.api.v1.pilot_milestones.enforce_pilot_mutation_rate_limit", AsyncMock())
    monkeypatch.setattr(
        "app.middleware.tenant.TenantScopeMiddleware._load_platform_session_access", AsyncMock(return_value=True)
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _platform_headers(session_id: uuid.UUID, *, csrf: bool = True) -> dict[str, str]:
    token = create_access_token(
        "platform",
        "platform-admin",
        "platform_admin",
        tenant_type="platform",
        extra={"sid": str(session_id)},
    )
    headers = {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
    }
    if csrf:
        headers["X-Platform-CSRF"] = "test-platform-csrf"
    return headers


def _body() -> dict:
    return {
        "milestone_type": "launched",
        "corrected_at": "2026-08-01T00:00:00Z",
        "source": "verified launch receipt",
        "reason": "correct source timestamp",
    }


@pytest.mark.asyncio
async def test_platform_correction_uses_cookie_session_and_target_tenant(client, monkeypatch):
    session_id = uuid.uuid4()
    target_tenant_id = uuid.uuid4()
    correction_id = uuid.uuid4()
    adapter = AsyncMock(
        return_value=SimpleNamespace(
            id=correction_id,
            milestone_type="launched",
            corrected_at=datetime(2026, 8, 1, tzinfo=UTC),
            source="verified launch receipt",
            reason="correct source timestamp",
        )
    )
    monkeypatch.setattr("app.api.v1.pilot_milestones.correct_milestone", adapter)

    response = await client.post(
        f"/api/v1/platform/tenants/{target_tenant_id}/pilot-milestones/corrections",
        headers=_platform_headers(session_id),
        json=_body(),
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(correction_id)
    kwargs = adapter.await_args.kwargs
    assert kwargs["platform_auth_session_id"] == session_id
    assert kwargs["idempotency_key"] == "11111111-1111-4111-8111-111111111111"
    assert len(kwargs["payload_digest"]) == 64
    assert adapter.await_args.args[1] == target_tenant_id


@pytest.mark.asyncio
async def test_platform_correction_rejects_bearer_and_missing_csrf_before_adapter(client, monkeypatch):
    adapter = AsyncMock()
    monkeypatch.setattr("app.api.v1.pilot_milestones.correct_milestone", adapter)
    target_tenant_id = uuid.uuid4()
    tenant_token = create_access_token(str(uuid.uuid4()), str(uuid.uuid4()), "admin", tenant_type="brand")

    bearer_response = await client.post(
        f"/api/v1/platform/tenants/{target_tenant_id}/pilot-milestones/corrections",
        headers={
            "Authorization": f"Bearer {tenant_token}",
            "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
        },
        json=_body(),
    )
    csrf_response = await client.post(
        f"/api/v1/platform/tenants/{target_tenant_id}/pilot-milestones/corrections",
        headers=_platform_headers(uuid.uuid4(), csrf=False),
        json=_body(),
    )

    assert bearer_response.status_code == 401
    assert csrf_response.status_code == 403
    adapter.assert_not_awaited()
