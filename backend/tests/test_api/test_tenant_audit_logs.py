"""租户操作审计 API：权限、隔离、筛选与分页。"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.tenant import Account
from app.services.audit import write_audit_log
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict[str, str]:
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _create_tenant(client: AsyncClient, db_session: AsyncSession, name: str) -> tuple[str, str]:
    response = await client.post(
        "/api/v1/tenants",
        json={
            "name": name,
            "admin_email": f"{name.lower()}@example.com",
            "admin_name": f"{name}管理员",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert response.status_code == 201
    tenant_id = response.json()["id"]
    account = (await db_session.execute(select(Account).where(Account.tenant_id == uuid.UUID(tenant_id)))).scalar_one()
    return tenant_id, str(account.id)


@pytest.mark.anyio
async def test_admin_only_sees_own_tenant_logs_with_readable_actor(
    client: AsyncClient,
    db_session: AsyncSession,
):
    tenant_a, admin_a = await _create_tenant(client, db_session, "TenantA")
    tenant_b, admin_b = await _create_tenant(client, db_session, "TenantB")
    await write_audit_log(
        db_session,
        operator_id=admin_a,
        target_tenant_id=tenant_a,
        action="organization_created",
        resource="organization:研发部",
        details={"result": "success"},
    )
    await write_audit_log(
        db_session,
        operator_id=admin_b,
        target_tenant_id=tenant_b,
        action="organization_deleted",
        resource="organization:秘密部门",
        details={"result": "success"},
    )
    await write_audit_log(
        db_session,
        operator_id="platform-admin",
        target_tenant_id=tenant_a,
        action="platform_tenant_status_changed",
        resource=f"tenant:{tenant_a}",
    )

    token = create_access_token(tenant_a, admin_a, "admin")
    response = await client.get(
        "/api/v1/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2  # tenant_create + organization_created
    assert all(item["tenant_id"] == tenant_a for item in payload["items"])
    assert not any("秘密部门" in item["resource"] for item in payload["items"])
    assert not any(item["action"].startswith("platform_") for item in payload["items"])
    created = next(item for item in payload["items"] if item["action"] == "organization_created")
    assert created["operator"]["id"] == admin_a
    assert created["operator"]["name"] == "TenantA管理员"
    assert created["result"] == "success"


@pytest.mark.anyio
async def test_audit_log_filters_and_pagination_are_applied(
    client: AsyncClient,
    db_session: AsyncSession,
):
    tenant_id, admin_id = await _create_tenant(client, db_session, "FilterTenant")
    await write_audit_log(
        db_session,
        operator_id=admin_id,
        target_tenant_id=tenant_id,
        action="product_created",
        resource="product:product-1",
        details={"resource_name": "五常大米"},
    )
    await write_audit_log(
        db_session,
        operator_id=admin_id,
        target_tenant_id=tenant_id,
        action="product_updated",
        resource="product:黑土地大米",
    )
    token = create_access_token(tenant_id, admin_id, "admin")
    start_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

    response = await client.get(
        "/api/v1/audit-logs",
        params={
            "action": "product_created",
            "keyword": "五常",
            "start_time": start_time,
            "page": 1,
            "page_size": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert [item["action"] for item in payload["items"]] == ["product_created"]


@pytest.mark.anyio
async def test_operator_cannot_read_tenant_audit_logs(client: AsyncClient):
    tenant_id = str(uuid.uuid4())
    token = create_access_token(tenant_id, str(uuid.uuid4()), "operator")

    response = await client.get(
        "/api/v1/audit-logs",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
