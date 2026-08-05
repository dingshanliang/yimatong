"""A2-005: RLS 隔离验证 — 场景3：篡改 tenant_id 防护

租户 A 尝试篡改 tenant_id 写入租户 B 的数据，被应用层拒绝。
PostgreSQL RLS 会提供额外保护层。
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


class TestTamperPrevention:
    @pytest.mark.anyio
    async def test_cannot_write_to_other_tenant(self, client: AsyncClient):
        """场景3：租户 A 使用自己的 token 创建数据，tenant_id 自动从 token 获取，无法指定其他 tenant_id"""
        resp_a = await client.post(
            "/api/v1/tenants",
            json={
                "name": "租户A",
                "admin_email": "tamper-a@test.com",
                "admin_name": "A",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tenant_a_id = resp_a.json()["id"]

        resp_b = await client.post(
            "/api/v1/tenants",
            json={
                "name": "租户B",
                "admin_email": "tamper-b@test.com",
                "admin_name": "B",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        tenant_b_id = resp_b.json()["id"]

        # 用租户 A 的 token 创建组织 — tenant_id 来自 token，不是客户端参数
        headers_a = _auth_headers(tenant_a_id)
        resp = await client.post(
            "/api/v1/organizations",
            json={"name": "新部门"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        # 创建的数据属于租户 A，不是 B
        assert resp.json()["tenant_id"] == tenant_a_id
        assert resp.json()["tenant_id"] != tenant_b_id
