"""A5-006: 页面预览与发布集成验证 API 测试"""

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


@pytest.fixture
async def auth_setup(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "预览测试",
            "admin_email": "preview@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


class TestPagePreview:
    @pytest.mark.anyio
    async def test_preview_with_published_version(self, client: AsyncClient, auth_setup):
        # 创建模板
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "预览模板", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]

        # 创建版本并发布
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={
                "config_json": {
                    "brand_name": "预览品牌",
                    "product_name": "预览产品",
                }
            },
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(
            f"/api/v1/page-versions/{vid}/publish",
            headers=auth_setup,
        )

        # 预览
        resp = await client.get(
            f"/api/v1/page-templates/{tid}/preview",
            headers=auth_setup,
        )
        assert resp.status_code == 200
        assert "预览品牌" in resp.text
        assert "预览产品" in resp.text

    @pytest.mark.anyio
    async def test_preview_no_published_version(self, client: AsyncClient, auth_setup):
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "空模板", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]

        resp = await client.get(
            f"/api/v1/page-templates/{tid}/preview",
            headers=auth_setup,
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_preview_with_mock_data(self, client: AsyncClient, auth_setup):
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "Mock模板", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]

        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"brand_name": "原始", "product_name": "原始产品"}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(
            f"/api/v1/page-versions/{vid}/publish",
            headers=auth_setup,
        )

        # 带 mock 数据预览
        resp = await client.get(
            f"/api/v1/page-templates/{tid}/preview?mock_brand=MockBrand",
            headers=auth_setup,
        )
        assert resp.status_code == 200


class TestPagePublishFlow:
    @pytest.mark.anyio
    async def test_end_to_end_flow(self, client: AsyncClient, auth_setup):
        """端到端：创建模板 -> 填充数据 -> 发布 -> 渲染"""
        # 1. 创建模板
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={
                "name": "E2E产品页",
                "template_type": "product_info",
                "description": "端到端测试",
            },
            headers=auth_setup,
        )
        assert tmpl.status_code == 201
        tid = tmpl.json()["id"]

        # 2. 创建版本
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={
                "config_json": {
                    "brand_name": "E2E品牌",
                    "product_name": "E2E有机苹果",
                    "specifications": {"weight": "500g"},
                }
            },
            headers=auth_setup,
        )
        assert ver.status_code == 201

        # 3. 发布版本
        vid = ver.json()["id"]
        pub = await client.post(
            f"/api/v1/page-versions/{vid}/publish",
            headers=auth_setup,
        )
        assert pub.status_code == 200
        assert pub.json()["status"] == "published"

        # 4. 获取模板详情，确认发布版本存在
        detail = await client.get(
            f"/api/v1/page-templates/{tid}",
            headers=auth_setup,
        )
        assert detail.json()["published_version"] is not None

        # 5. 渲染预览
        preview = await client.get(
            f"/api/v1/page-templates/{tid}/preview",
            headers=auth_setup,
        )
        assert preview.status_code == 200
        assert "E2E品牌" in preview.text
        assert "E2E有机苹果" in preview.text
        assert "500g" in preview.text
