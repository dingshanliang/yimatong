"""A2-006: 平台管理员跨租户操作验收测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _platform_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


class TestPlatformAdminAuth:
    @pytest.mark.anyio
    async def test_platform_login_success(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    @pytest.mark.anyio
    async def test_platform_login_invalid_credentials(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "wrong"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_platform_token_contains_platform_admin_role(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )
        token = resp.json()["access_token"]
        from app.utils.security import decode_token

        payload = decode_token(token)
        assert payload["role"] == "platform_admin"


class TestPlatformAuditLogs:
    @pytest.mark.anyio
    async def test_list_audit_logs(self, client: AsyncClient, db_session: AsyncSession):
        from app.services.audit import write_audit_log

        await write_audit_log(db_session, "admin-1", "tenant-1", "read", "tenants/123")
        resp = await client.get("/api/v1/platform/audit-logs", headers=_platform_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["operator_id"] == "admin-1"
        assert data[0]["action"] == "read"

    @pytest.mark.anyio
    async def test_audit_logs_require_platform_admin(self, client: AsyncClient):
        regular_token = create_access_token("tenant-1", "user-1", "admin")
        headers = {"Authorization": f"Bearer {regular_token}"}
        resp = await client.get("/api/v1/platform/audit-logs", headers=headers)
        assert resp.status_code == 403
