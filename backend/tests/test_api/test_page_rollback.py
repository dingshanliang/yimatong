"""B2-003: 页面回滚 API 测试"""

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
async def setup_template_with_versions(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "回滚测试",
            "admin_email": "rollback@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    tmpl = await client.post(
        "/api/v1/page-templates",
        json={"name": "回滚模板", "template_type": "product_info"},
        headers=headers,
    )
    template_id = tmpl.json()["id"]

    # 创建 v1 并发布
    v1 = await client.post(
        f"/api/v1/page-templates/{template_id}/versions",
        json={"config_json": {"dsl_version": "1.0", "title": "Version 1"}},
        headers=headers,
    )
    v1_id = v1.json()["id"]
    await client.post(
        f"/api/v1/page-versions/{v1_id}/publish",
        headers=headers,
    )

    # 创建 v2 并发布
    v2 = await client.post(
        f"/api/v1/page-templates/{template_id}/versions",
        json={"config_json": {"dsl_version": "1.0", "title": "Version 2"}},
        headers=headers,
    )
    v2_id = v2.json()["id"]
    await client.post(
        f"/api/v1/page-versions/{v2_id}/publish",
        headers=headers,
    )

    # 创建 v3 (draft)
    await client.post(
        f"/api/v1/page-templates/{template_id}/versions",
        json={"config_json": {"dsl_version": "1.0", "title": "Version 3"}},
        headers=headers,
    )

    return tid, headers, template_id, v1_id


class TestPageRollback:
    @pytest.mark.anyio
    async def test_rollback_creates_new_version(self, client: AsyncClient, setup_template_with_versions):
        _, headers, template_id, v1_id = setup_template_with_versions

        resp = await client.post(
            f"/api/v1/page-templates/{template_id}/versions/{v1_id}/rollback",
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["config_json"]["title"] == "Version 1"
        assert data["status"] == "draft"
        assert data["version"] == 4

    @pytest.mark.anyio
    async def test_rollback_preserves_history(self, client: AsyncClient, setup_template_with_versions):
        _, headers, template_id, v1_id = setup_template_with_versions

        versions_resp = await client.get(
            f"/api/v1/page-templates/{template_id}/versions",
            headers=headers,
        )
        original_count = len(versions_resp.json())

        await client.post(
            f"/api/v1/page-templates/{template_id}/versions/{v1_id}/rollback",
            headers=headers,
        )

        versions_resp = await client.get(
            f"/api/v1/page-templates/{template_id}/versions",
            headers=headers,
        )
        assert len(versions_resp.json()) == original_count + 1

    @pytest.mark.anyio
    async def test_rollback_nonexistent_version_404(self, client: AsyncClient, setup_template_with_versions):
        _, headers, template_id, _ = setup_template_with_versions

        resp = await client.post(
            f"/api/v1/page-templates/{template_id}/versions/00000000-0000-0000-0000-000000000099/rollback",
            headers=headers,
        )
        assert resp.status_code == 404
