"""Tenant risk-rule API boundary tests.

Mutation semantics are exercised against fresh PostgreSQL in
test_acceptance/test_risk_asgi_authority.py; SQLite tests stay focused on
feature/auth routing and never emulate the database authority.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value
    app.dependency_overrides.clear()


async def _create_tenant(client: AsyncClient, *, risk_enabled: bool) -> str:
    response = await client.post(
        "/api/v1/tenants",
        json={
            "name": "风控边界测试租户",
            "admin_email": "risk-boundary@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tenant_id = response.json()["id"]
    if risk_enabled:
        enabled = await client.patch(
            f"/api/v1/tenants/{tenant_id}",
            json={"enabled_features": {"risk_module": True}},
            headers=_platform_admin_headers(),
        )
        assert enabled.status_code == 200
    return tenant_id


@pytest.mark.anyio
async def test_risk_api_requires_the_paid_feature(client: AsyncClient):
    tenant_id = await _create_tenant(client, risk_enabled=False)
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    response = await client.get("/api/v1/risk-rules", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TENANT_FEATURE_DISABLED"


@pytest.mark.anyio
async def test_viewer_is_denied_before_risk_data_query(client: AsyncClient, monkeypatch):
    tenant_id = await _create_tenant(client, risk_enabled=True)
    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock())
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000002", "viewer")
    response = await client.get("/api/v1/risk-rules", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing permission: risk:read"


@pytest.mark.anyio
async def test_arbitrary_context_mutation_endpoints_are_removed(client: AsyncClient, monkeypatch):
    tenant_id = await _create_tenant(client, risk_enabled=True)
    monkeypatch.setattr("app.services.risk_access.require_durable_session", AsyncMock())
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000003", "admin")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "legacy-evaluate-denied"}
    response = await client.post(
        "/api/v1/risk-rules/evaluate",
        json={"rule_type": "ip_frequency", "context": {"request_count": 999999}},
        headers=headers,
    )
    assert response.status_code in {404, 405, 422}
