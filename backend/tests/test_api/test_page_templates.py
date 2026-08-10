"""A5-002: PageTemplate CRUD API + A5-003: PageVersion 版本管理 验收测试"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.page import PageTemplate
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
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


async def create_product(client: AsyncClient, headers: dict[str, str], name: str = "五常大米") -> str:
    brand = await client.post("/api/v1/brands", json={"name": f"{name}品牌"}, headers=headers)
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": name, "category": "大米"},
        headers=headers,
    )
    return product.json()["id"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/page-templates/industry-templates/{index}/clone",
        "/api/v1/page-templates/{template_id}/versions",
        "/api/v1/page-templates/{template_id}/versions/{version_id}/rollback",
    ],
)
def test_page_version_mutations_commit_before_success_response(path: str):
    route = next(
        route for route in app.routes if isinstance(route, APIRoute) and route.path == path and "POST" in route.methods
    )
    db_dependency = next(dependency for dependency in route.dependant.dependencies if dependency.call is get_db)

    assert db_dependency.scope == "function"


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
    async def test_list_templates_returns_publish_workbench_fields(self, client: AsyncClient, auth_setup):
        product_id = await create_product(client, auth_setup)
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "扫码信任页", "template_type": "traceability", "product_id": product_id},
            headers=auth_setup,
        )
        tid = create.json()["id"]
        published = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"v": "published"}},
            headers=auth_setup,
        )
        await client.post(f"/api/v1/page-versions/{published.json()['id']}/publish", headers=auth_setup)
        await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"v": "draft"}},
            headers=auth_setup,
        )

        resp = await client.get("/api/v1/page-templates", headers=auth_setup)

        assert resp.status_code == 200
        item = next(t for t in resp.json()["items"] if t["id"] == tid)
        assert item["product_name"] == "五常大米"
        assert item["published_version"]["status"] == "published"
        assert item["published_version"]["published_at"] is not None
        assert item["draft_version"]["status"] == "draft"
        assert item["display_status"] == "has_unpublished_draft"
        assert item["updated_at"] is not None

    @pytest.mark.anyio
    async def test_list_templates_filter_by_type(self, client: AsyncClient, auth_setup):
        await client.post(
            "/api/v1/page-templates",
            json={"name": "T3", "template_type": "product_info"},
            headers=auth_setup,
        )
        resp = await client.get(
            "/api/v1/page-templates?template_type=product_info",
            headers=auth_setup,
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
            f"/api/v1/page-templates/{tid}",
            headers=auth_setup,
        )
        assert resp.status_code == 200

        # 验证软删除：列表中默认过滤 archived
        detail = await client.get(
            f"/api/v1/page-templates/{tid}",
            headers=auth_setup,
        )
        assert detail.json()["status"] == "archived"

    @pytest.mark.anyio
    async def test_get_template_not_found(self, client: AsyncClient, auth_setup):
        fake_id = "00000000-0000-0000-0000-000000000999"
        resp = await client.get(
            f"/api/v1/page-templates/{fake_id}",
            headers=auth_setup,
        )
        assert resp.status_code == 404


class TestPageVersionManagement:
    @pytest.mark.anyio
    async def test_create_version_locks_parent_before_unlocked_version_aggregate(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_setup,
        monkeypatch: pytest.MonkeyPatch,
    ):
        create = await client.post(
            "/api/v1/page-templates",
            json={"name": "并发版本测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        template_id = create.json()["id"]
        statements = []
        original_execute = db_session.execute

        async def capture_execute(statement, *args, **kwargs):
            statements.append(statement)
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db_session, "execute", capture_execute)

        response = await client.post(
            f"/api/v1/page-templates/{template_id}/versions",
            json={"config_json": {"brand_name": "测试品牌"}},
            headers=auth_setup,
        )

        assert response.status_code == 201
        postgres_sql = [str(statement.compile(dialect=postgresql.dialect())) for statement in statements]
        parent_locks = [
            sql
            for sql in postgres_sql
            if "FROM page_templates" in sql
            and "page_templates.id =" in sql
            and "page_templates.tenant_id =" in sql
            and "FOR UPDATE" in sql
        ]
        version_aggregates = [sql for sql in postgres_sql if "max(page_versions.version)" in sql]
        assert len(parent_locks) == 1
        assert len(version_aggregates) == 1
        assert "FOR UPDATE" not in version_aggregates[0]

    @pytest.mark.anyio
    async def test_create_version_returns_404_for_missing_template(self, client: AsyncClient, auth_setup):
        response = await client.post(
            f"/api/v1/page-templates/{uuid.uuid4()}/versions",
            json={"config_json": {"brand_name": "测试品牌"}},
            headers=auth_setup,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Page template not found"

    @pytest.mark.anyio
    async def test_create_version_returns_404_for_cross_tenant_template(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        auth_setup,
    ):
        other_template = PageTemplate(
            tenant_id=uuid.uuid4(),
            name="其他租户页面",
            template_type="product_info",
        )
        db_session.add(other_template)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/page-templates/{other_template.id}/versions",
            json={"config_json": {"brand_name": "测试品牌"}},
            headers=auth_setup,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Page template not found"

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
            f"/api/v1/page-versions/{vid}/publish",
            headers=auth_setup,
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
            f"/api/v1/page-versions/{v1.json()['id']}/publish",
            headers=auth_setup,
        )
        # 发布 v2（自动归档 v1）
        await client.post(
            f"/api/v1/page-versions/{v2.json()['id']}/publish",
            headers=auth_setup,
        )

        # v1 应该被自动归档
        versions = await client.get(
            f"/api/v1/page-templates/{tid}/versions",
            headers=auth_setup,
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
            f"/api/v1/page-versions/{vid}/archive",
            headers=auth_setup,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    @pytest.mark.anyio
    async def test_template_detail_with_published_version(self, client: AsyncClient, auth_setup):
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
            f"/api/v1/page-templates/{tid}",
            headers=auth_setup,
        )
        assert detail.json()["published_version"] is not None
        assert detail.json()["published_version"]["status"] == "published"
        assert detail.json()["published_version"]["published_at"] is not None

    @pytest.mark.anyio
    async def test_clone_industry_template_can_bind_product_and_name(self, client: AsyncClient, auth_setup):
        product_id = await create_product(client, auth_setup, "礼盒大米")
        resp = await client.post(
            "/api/v1/page-templates/industry-templates/0/clone",
            json={"name": "礼盒扫码页", "product_id": product_id},
            headers=auth_setup,
        )

        assert resp.status_code == 201
        template = resp.json()["template"]
        assert template["name"] == "礼盒扫码页"
        assert template["product_id"] == product_id

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
            f"/api/v1/page-templates/{tid}/versions",
            headers=auth_setup,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        # 最新版本排在前面
        assert resp.json()[0]["version"] == 2
