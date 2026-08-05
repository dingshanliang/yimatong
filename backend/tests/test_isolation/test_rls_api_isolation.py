"""A2-005: RLS 隔离验证 — 场景1：API 级别隔离

租户 A 创建的数据，租户 B 通过 API 无法读取。
此测试验证应用层的 tenant_id 过滤逻辑（SQLite 可测）。
PostgreSQL RLS 策略验证需要真实 PG 环境。
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers(tenant_id: str) -> dict:
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


class TestAPIIsolation:
    @pytest.mark.anyio
    async def test_tenant_a_data_not_visible_to_tenant_b(self, client: AsyncClient):
        """场景1：租户 A 创建的数据，租户 B 通过 API 无法读取"""
        # 租户 A 创建组织
        resp_a = await client.post(
            "/api/v1/tenants",
            json={
                "name": "租户A",
                "admin_email": "a@iso.com",
                "admin_name": "A",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert resp_a.status_code == 201
        tenant_a_id = resp_a.json()["id"]

        headers_a = _auth_headers(tenant_a_id)
        await client.post("/api/v1/organizations", json={"name": "A的秘密部门"}, headers=headers_a)

        # 租户 B 查询
        resp_b = await client.post(
            "/api/v1/tenants",
            json={
                "name": "租户B",
                "admin_email": "b@iso.com",
                "admin_name": "B",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tenant_b_id = resp_b.json()["id"]
        headers_b = _auth_headers(tenant_b_id)

        orgs_b = await client.get("/api/v1/organizations", headers=headers_b)
        assert orgs_b.status_code == 200
        names = [o["name"] for o in orgs_b.json()["items"]]
        assert "A的秘密部门" not in names
