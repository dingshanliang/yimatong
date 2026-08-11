"""页面引擎安全与 DSL 校验测试

覆盖：公开端点安全、版本不可变、状态机、XSS 消毒、DSL 校验、版本号唯一
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.audit import PlatformAuditLog
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
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
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "安全测试",
            "admin_email": "sec@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    return {"Authorization": f"Bearer {token}"}


# ── 公开端点安全测试 ──────────────────────────────────────


class TestPublicEndpointSecurity:
    @pytest.mark.anyio
    async def test_public_page_endpoint_requires_tenant_authentication(self, client: AsyncClient):
        response = await client.get("/api/v1/public/pages/00000000-0000-0000-0000-000000000001")

        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_public_endpoint_only_returns_published(self, client: AsyncClient, auth_setup):
        """草稿版本不可通过公开端点访问"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "公开测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]

        # draft → 公开端点应返回 404（即使带 auth，published 过滤仍然生效）
        resp = await client.get(f"/api/v1/public/pages/{vid}", headers=auth_setup)
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_public_endpoint_returns_published(self, client: AsyncClient, auth_setup):
        """published 版本可通过公开端点访问"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "公开发布", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": [{"id": "h", "type": "product_hero", "enabled": True}]}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)

        resp = await client.get(f"/api/v1/public/pages/{vid}", headers=auth_setup)
        assert resp.status_code == 200
        assert resp.json()["config_json"]["modules"][0]["type"] == "product_hero"

    @pytest.mark.anyio
    async def test_authenticated_page_config_is_not_cross_tenant_enumerable(self, client: AsyncClient, auth_setup):
        other = await client.post(
            "/api/v1/tenants",
            json={
                "name": "其他页面租户",
                "admin_email": "other-page@test.com",
                "admin_name": "Other Admin",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        other_tenant_id = other.json()["id"]
        other_headers = {
            "Authorization": f"Bearer {create_access_token(other_tenant_id, '00000000-0000-0000-0000-000000000002', 'admin')}"
        }
        template = await client.post(
            "/api/v1/page-templates",
            json={"name": "其他租户配置", "template_type": "product_info"},
            headers=other_headers,
        )
        version = await client.post(
            f"/api/v1/page-templates/{template.json()['id']}/versions",
            json={"config_json": {"modules": []}},
            headers=other_headers,
        )
        version_id = version.json()["id"]
        await client.post(f"/api/v1/page-versions/{version_id}/publish", headers=other_headers)

        response = await client.get(f"/api/v1/public/pages/{version_id}", headers=auth_setup)

        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_public_endpoint_archived_returns_404(self, client: AsyncClient, auth_setup):
        """归档版本不可通过公开端点访问"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "归档公开", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        await client.post(f"/api/v1/page-versions/{vid}/archive", headers=auth_setup)

        resp = await client.get(f"/api/v1/public/pages/{vid}", headers=auth_setup)
        assert resp.status_code == 404


# ── 版本不可变测试 ──────────────────────────────────────


class TestVersionImmutability:
    @pytest.mark.anyio
    async def test_update_published_version_rejected(self, client: AsyncClient, auth_setup):
        """PATCH 已发布版本应返回 400"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "不可变测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)

        resp = await client.patch(
            f"/api/v1/page-versions/{vid}",
            json={"config_json": {"modules": [{"id": "x", "type": "product_hero"}]}},
            headers=auth_setup,
        )
        assert resp.status_code == 400
        assert "Cannot modify" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_update_archived_version_rejected(self, client: AsyncClient, auth_setup):
        """PATCH 归档版本应返回 400"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "归档不可变", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        await client.post(f"/api/v1/page-versions/{vid}/archive", headers=auth_setup)

        resp = await client.patch(
            f"/api/v1/page-versions/{vid}",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        assert resp.status_code == 400


# ── 版本状态机测试 ──────────────────────────────────────


class TestVersionStateMachine:
    @pytest.mark.anyio
    async def test_publish_already_published_rejected(self, client: AsyncClient, auth_setup):
        """发布已发布版本应返回 409"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "重复发布", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)

        # 再次发布
        resp = await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_publish_archived_rejected(self, client: AsyncClient, auth_setup):
        """发布归档版本应返回 409"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "发布归档", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        await client.post(f"/api/v1/page-versions/{vid}/archive", headers=auth_setup)

        resp = await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_archive_already_archived_rejected(self, client: AsyncClient, auth_setup):
        """归档已归档版本应返回 409"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "重复归档", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]
        await client.post(f"/api/v1/page-versions/{vid}/archive", headers=auth_setup)

        resp = await client.post(f"/api/v1/page-versions/{vid}/archive", headers=auth_setup)
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_valid_draft_to_published(self, client: AsyncClient, auth_setup):
        """draft → published 合法"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "合法发布", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]

        resp = await client.post(f"/api/v1/page-versions/{vid}/publish", headers=auth_setup)
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"


class TestPageMutationAudit:
    @pytest.mark.anyio
    async def test_page_mutations_are_actor_bound_and_audited(
        self, client: AsyncClient, db_session: AsyncSession, auth_setup
    ):
        template = await client.post(
            "/api/v1/page-templates",
            json={"name": "审计页面", "template_type": "traceability"},
            headers=auth_setup,
        )
        template_id = template.json()["id"]
        await client.patch(
            f"/api/v1/page-templates/{template_id}",
            json={"name": "审计页面更新"},
            headers=auth_setup,
        )
        version = await client.post(
            f"/api/v1/page-templates/{template_id}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        version_id = version.json()["id"]
        await client.patch(
            f"/api/v1/page-versions/{version_id}",
            json={"config_json": {"modules": [], "routing": {"default_page": True}}},
            headers=auth_setup,
        )
        await client.post(f"/api/v1/page-versions/{version_id}/publish", headers=auth_setup)
        await client.post(f"/api/v1/page-versions/{version_id}/archive", headers=auth_setup)
        await client.post(
            f"/api/v1/page-templates/{template_id}/versions/{version_id}/rollback",
            headers=auth_setup,
        )
        await client.delete(f"/api/v1/page-templates/{template_id}", headers=auth_setup)

        actions = list((await db_session.scalars(select(PlatformAuditLog.action))).all())

        assert set(actions) >= {
            "page_template_created",
            "page_template_updated",
            "page_template_archived",
            "page_version_created",
            "page_version_updated",
            "page_version_published",
            "page_version_archived",
            "page_version_rolled_back",
        }


# ── XSS 消毒测试 ──────────────────────────────────────


class TestXSSSanitization:
    @pytest.mark.anyio
    async def test_script_tag_stripped_on_create(self, client: AsyncClient, auth_setup):
        """创建版本时 <script> 标签被剥离"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "XSS测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={
                "config_json": {
                    "modules": [
                        {
                            "id": "custom1",
                            "type": "custom_html",
                            "enabled": True,
                            "config": {"html": '<p>Hello</p><script>alert("XSS")</script>'},
                        }
                    ],
                }
            },
            headers=auth_setup,
        )
        config = ver.json()["config_json"]
        html = config["modules"][0]["config"]["html"]
        assert "<script>" not in html
        assert "alert" not in html
        assert "<p>Hello</p>" in html

    @pytest.mark.anyio
    async def test_script_tag_stripped_on_update(self, client: AsyncClient, auth_setup):
        """更新草稿时 <script> 标签被剥离"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "XSS更新", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": []}},
            headers=auth_setup,
        )
        vid = ver.json()["id"]

        resp = await client.patch(
            f"/api/v1/page-versions/{vid}",
            json={
                "config_json": {
                    "modules": [
                        {
                            "id": "c2",
                            "type": "custom_html",
                            "enabled": True,
                            "config": {"html": '<div onclick="evil()">test</div>'},
                        }
                    ],
                }
            },
            headers=auth_setup,
        )
        html = resp.json()["config_json"]["modules"][0]["config"]["html"]
        assert "onclick" not in html
        assert "<div>test</div>" in html

    @pytest.mark.anyio
    async def test_iframe_stripped(self, client: AsyncClient, auth_setup):
        """iframe 标签被剥离"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "iframe测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        ver = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={
                "config_json": {
                    "modules": [
                        {
                            "id": "c3",
                            "type": "custom_html",
                            "enabled": True,
                            "config": {"html": '<iframe src="https://evil.com"></iframe><p>Safe</p>'},
                        }
                    ],
                }
            },
            headers=auth_setup,
        )
        html = ver.json()["config_json"]["modules"][0]["config"]["html"]
        assert "<iframe" not in html
        assert "<p>Safe</p>" in html


# ── DSL 校验测试 ──────────────────────────────────────


class TestDSLValidation:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "module",
        [
            {
                "id": "risk",
                "type": "risk_alert",
                "enabled": True,
                "config": {"alert_type": "suspected_copy", "detail": "伪造风险事实", "scan_count": 999},
            },
            {
                "id": "verify",
                "type": "dual_code_verify",
                "enabled": True,
                "config": {"product_verified": True},
            },
        ],
    )
    async def test_authoritative_facts_cannot_be_persisted_in_page_dsl(
        self, client: AsyncClient, auth_setup, module: dict
    ):
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "权威事实边界", "template_type": "traceability"},
            headers=auth_setup,
        )

        response = await client.post(
            f"/api/v1/page-templates/{tmpl.json()['id']}/versions",
            json={"config_json": {"modules": [module]}},
            headers=auth_setup,
        )

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_invalid_module_type_rejected(self, client: AsyncClient, auth_setup):
        """非法模块类型被后端拒绝"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "DSL测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        resp = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={
                "config_json": {
                    "modules": [{"id": "x", "type": "nonexistent_module", "enabled": True}],
                }
            },
            headers=auth_setup,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_module_missing_id_rejected(self, client: AsyncClient, auth_setup):
        """模块缺少 id 字段被拒绝"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "缺ID测试", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        resp = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={"config_json": {"modules": [{"type": "product_hero", "enabled": True}]}},
            headers=auth_setup,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_valid_dsl_accepted(self, client: AsyncClient, auth_setup):
        """合法 DSL 被接受"""
        tmpl = await client.post(
            "/api/v1/page-templates",
            json={"name": "合法DSL", "template_type": "product_info"},
            headers=auth_setup,
        )
        tid = tmpl.json()["id"]
        resp = await client.post(
            f"/api/v1/page-templates/{tid}/versions",
            json={
                "config_json": {
                    "modules": [
                        {"id": "hero", "type": "product_hero", "enabled": True, "config": {"show_verify_badge": True}},
                        {"id": "legal", "type": "legal_terms", "enabled": True},
                    ],
                    "routing": {"default_page": True},
                }
            },
            headers=auth_setup,
        )
        assert resp.status_code == 201
        assert len(resp.json()["config_json"]["modules"]) == 2
