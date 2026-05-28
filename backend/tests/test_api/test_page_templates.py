"""A5-002: PageTemplate CRUD API + A5-003: PageVersion 版本管理 验收测试"""

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
    """创建租户并返回 headers"""
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "模板测试",
            "admin_email": "template@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


class TestPageTemplateCRUD:
    @pytest.mark.anyio
    async def test_create_template(self, client: AsyncClient, auth_setup):
        resp = await client.post(
            "/api/v1/page-templates",
            json={
                "name": "产品信息页",
                "template_type": "product_info",
                "description": "展示产品基本信息",
            },
            headers=auth_setup,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "产品信息页"
        assert data["template_type"] == "product_info"
        assert data["status"] == "active"

    @pytest.mark.anyio
    async def test_list_templates(self, client: AsyncClient, auth_setup):
        await client.post(
            "/api/v1/page-templates",
            json={"name": "T1", "template_type": "product_info"},
            headers=auth_setup,
        )
        await client.post(
            "/api/v1/page-templates",
            json={"name": "T2", "template_type": "brand_story"},
            headers=auth_setup,
        )
        resp = await client.get("/api/v1/page-templates", headers=auth_setup)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2

    @pytest.mark.anyio
    async def test_list_templates_filter_by_type(self, client: AsyncClient, auth_setup):
        await client.post(
            "/api/v1/page-templates",
            json={"name": "T3", "template_type": "product_info"},
            headers=auth_setup,
        )
        resp = await client.get(
            "/api/v1/page-templates?template_type=product_info", headers=auth_setup,
        )
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["template_type"] == "product_info"

    @pytest.mark.anyio
    async def test_get_template_detail(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "详情页", "template_type": "traceability"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        resp = await client.get(f"/api/v1/page-templates/{tid}", headers=auth_setup)
        assert resp.status_code == 200
        assert resp.json()["name"] == "详情页"
        assert resp.json()["published_version"] is None

    @pytest.mark.anyio
    async def test_update_template(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "旧名", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        resp = await client.patch(
            f"/api/v1/page-templates/{tid}",
            json={"name": "新名"},
            headers=auth_setup,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"

    @pytest.mark.anyio
    async def test_delete_template_soft(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "待删", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        resp = await client.delete(
            f"/api/v1/page-templates/{tid}", headers=auth_setup,
        )
        assert resp.status_code == 200

        # 验证软删除：列表中默认过滤 archived
        detail = await client.get(
            f"/api/v1/page-templates/{tid}", headers=auth_setup,
        )
        assert detail.json()["status"] == "archived"

    @pytest.mark.anyio
    async def test_get_template_not_found(self, client: AsyncClient, auth_setup):
        fake_id = "00000000-0000-0000-0000-000000000999"
        resp = await client.get(
            f"/api/v1/page-templates/{fake_id}", headers=auth_setup,
        )
        assert resp.status_code == 404


class TestPageVersionManagement:
    @pytest.mark.anyio
    async def test_create_version(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "版本测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        resp = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"brand_name": "测试品牌"}},
            headers=auth_setup,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["version"] == 1
        assert data["status"] == "draft"
        assert data["config_json"]["brand_name"] == "测试品牌"

    @pytest.mark.anyio
    async def test_version_auto_increment(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "自增测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        v1 = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"v": 1}},
            headers=auth_setup,
        )
        v2 = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"v": 2}},
            headers=auth_setup,
        )
        assert v1.json()["version"] == 1
        assert v2.json()["version"] == 2

    @pytest.mark.anyio
    async def test_update_version_config(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "更新测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"old": True}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        resp = await client.patch(
            f"/api/v1/page-versions/{vid}",
            json={"config_json": {"new": True}},
            headers=auth_setup,
        )
        assert resp.status_code == 200
        assert resp.json()["config_json"]["new"] is True

    @pytest.mark.anyio
    async def test_publish_version(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "发布测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"data": "value"}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        resp = await client.post(
            f"/api/v1/page-versions/{vid}/publish", headers=auth_setup,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"

    @pytest.mark.anyio
    async def test_publish_archives_old(self, client: AsyncClient, auth_setup):
        """同一模板仅允许一个 published 版本"""
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "单发布测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]

        v1 = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"ver": 1}},
            headers=auth_setup,
        )
        v2 = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"ver": 2}},
            headers=auth_setup,
        )

        # 发布 v1
        await client.post(
            f"/api/v1/page-versions/{v1.json()['id']}/publish", headers=auth_setup,
        )
        # 发布 v2（自动归档 v1）
        await client.post(
            f"/api/v1/page-versions/{v2.json()['id']}/publish", headers=auth_setup,
        )

        # v1 应该被自动归档
        versions = await client.get(
            f"/api/v1/page-templates/{tid}/versions", headers=auth_setup,
        )
        statuses = {v["version"]: v["status"] for v in versions.json()}
        assert statuses[1] == "archived"
        assert statuses[2] == "published"

    @pytest.mark.anyio
    async def test_archive_version(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "归档测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]

        # 先发布再归档
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        resp = await client.post(
            f"/api/v1/page-versions/{vid}/archive", headers=auth_setup,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    @pytest.mark.anyio
    async def test_template_detail_with_published_version(
        self, client: AsyncClient, auth_setup
    ):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "发布详情", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"brand": "test"}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)

        detail = await client.get(
            f"/api/v1/page-templates/{tid}", headers=auth_setup,
        )
        assert detail.json()["published_version"] is not None
        assert detail.json()["published_version"]["status"] == "published"

    @pytest.mark.anyio
    async def test_list_versions(self, client: AsyncClient, auth_setup):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "版本列表", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"v": 1}},
            headers=auth_setup,
        )
        await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"v": 2}},
            headers=auth_setup,
        )
        resp = await client.get(
            f"/api/v1/page-templates/{tid}/versions", headers=auth_setup,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        # 最新版本排在前面
        assert resp.json()[0]["version"] == 2
