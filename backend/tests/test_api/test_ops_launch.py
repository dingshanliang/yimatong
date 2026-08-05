"""B5: 代运营工作台与上线检查测试"""

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


@pytest.fixture
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "上线检查测试",
            "admin_email": "launch@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    # tenant token for brand/product creation
    tenant_token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin", tenant_type="brand")
    # ops token for ops endpoints
    ops_token = create_access_token(
        "00000000-0000-0000-0000-000000000000",
        "00000000-0000-0000-0000-000000000001",
        "platform_admin",
        tenant_type="platform",
    )
    return tid, {"Authorization": f"Bearer {tenant_token}"}, {"Authorization": f"Bearer {ops_token}"}


class TestLaunchChecklist:
    @pytest.mark.anyio
    async def test_empty_checklist(self, client: AsyncClient, setup_tenant):
        tid, _tenant_headers, ops_headers = setup_tenant
        resp = await client.get(
            f"/api/v1/ops/clients/{tid}/launch-checklist",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is False
        assert data["passed_count"] == 0

    @pytest.mark.anyio
    async def test_checklist_after_product(self, client: AsyncClient, setup_tenant):
        tid, tenant_headers, ops_headers = setup_tenant
        # 创建品牌和产品（用 tenant token）
        brand = await client.post(
            "/api/v1/brands",
            json={"name": "测试品牌"},
            headers=tenant_headers,
        )
        await client.post(
            "/api/v1/products",
            json={"brand_id": brand.json()["id"], "name": "测试产品"},
            headers=tenant_headers,
        )

        resp = await client.get(
            f"/api/v1/ops/clients/{tid}/launch-checklist",
            headers=ops_headers,
        )
        data = resp.json()
        brand_check = next(c for c in data["checks"] if "品牌" in c["name"])
        product_check = next(c for c in data["checks"] if "产品" in c["name"])
        assert brand_check["passed"] is True
        assert product_check["passed"] is True

    @pytest.mark.anyio
    async def test_tenant_status(self, client: AsyncClient, setup_tenant):
        tid, _tenant_headers, ops_headers = setup_tenant
        resp = await client.get(
            f"/api/v1/ops/clients/{tid}/status",
            headers=ops_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "products" in data
        assert "published_pages" in data
        assert "activated_batches" in data
