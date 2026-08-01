"""A2-004: Tenant scope 注入与自动查询过滤"""

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


def _auth_header(tenant_id: str, account_id: str = "00000000-0000-0000-0000-000000000001") -> dict:
    token = create_access_token(tenant_id, account_id, "admin")
    return {"Authorization": f"Bearer {token}"}


class TestTenantScopeInjection:
    @pytest.mark.anyio
    async def test_tenant_id_injected_from_token(self, client: AsyncClient, db_session: AsyncSession):
        """中间件从 Bearer Token 解析 tenant_id 注入请求上下文"""
        from uuid6 import uuid7

        from app.models.tenant import Organization, Tenant

        tenant = Tenant(id=uuid7(), name="注入测试", slug=f"inject-{uuid7().hex[:8]}")
        db_session.add(tenant)
        await db_session.flush()
        org = Organization(id=uuid7(), tenant_id=tenant.id, name="部门")
        db_session.add(org)
        await db_session.commit()

        headers = _auth_header(str(tenant.id))
        resp = await client.get("/api/v1/organizations", headers=headers)
        assert resp.status_code == 200
        orgs = resp.json()["items"]
        assert len(orgs) >= 1
        assert all(o["tenant_id"] == str(tenant.id) for o in orgs)

    @pytest.mark.anyio
    async def test_create_auto_fills_tenant_id(self, client: AsyncClient, db_session: AsyncSession):
        """创建记录时自动填充 tenant_id 字段（无需客户端传入）"""
        from uuid6 import uuid7

        from app.models.tenant import Organization, Tenant

        tenant = Tenant(id=uuid7(), name="自动填充", slug=f"auto-{uuid7().hex[:8]}")
        db_session.add(tenant)
        await db_session.flush()
        org = Organization(id=uuid7(), tenant_id=tenant.id, name="种子部门")
        db_session.add(org)
        await db_session.commit()

        headers = _auth_header(str(tenant.id))
        resp = await client.post(
            "/api/v1/organizations",
            json={"name": "新部门"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["tenant_id"] == str(tenant.id)

    @pytest.mark.anyio
    async def test_cross_tenant_returns_empty(self, client: AsyncClient, db_session: AsyncSession):
        """尝试访问其他租户数据返回空结果集而非报错"""
        from uuid6 import uuid7

        from app.models.tenant import Organization, Tenant

        tenant_a = Tenant(id=uuid7(), name="租户A", slug=f"tenant-a-{uuid7().hex[:8]}")
        tenant_b = Tenant(id=uuid7(), name="租户B", slug=f"tenant-b-{uuid7().hex[:8]}")
        db_session.add_all([tenant_a, tenant_b])
        await db_session.flush()

        db_session.add(Organization(id=uuid7(), tenant_id=tenant_a.id, name="A的部门"))
        await db_session.commit()

        # 用租户 B 的 token 查询，应该看不到租户 A 的数据
        headers_b = _auth_header(str(tenant_b.id))
        resp = await client.get("/api/v1/organizations", headers=headers_b)
        assert resp.status_code == 200
        orgs = resp.json()["items"]
        assert all(o["tenant_id"] != str(tenant_a.id) for o in orgs)

    @pytest.mark.anyio
    async def test_missing_token_returns_401(self, client: AsyncClient):
        """无 token 访问受保护路由返回 401"""
        resp = await client.get("/api/v1/organizations")
        assert resp.status_code == 401
