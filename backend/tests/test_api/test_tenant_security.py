"""Tenants 安全边界测试 — 权限隔离、/me 字段限制、slug 唯一性。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token

# ── Helpers ────────────────────────────────────────────────────────


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


def _tenant_admin_headers(tenant_id: str, role: str = "admin") -> dict:
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", role)
    return {"Authorization": f"Bearer {token}"}


def _unique_slug() -> str:
    return f"sec-test-{uuid.uuid4().hex[:8]}"


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
async def client(db: AsyncSession):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def sample_tenant(client: AsyncClient):
    """通过 platform_admin 创建一个示例租户（slug 唯一）。"""
    slug = _unique_slug()
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "安全测试租户",
            "slug": slug,
            "plan": "free",
            "admin_email": "secadmin@test.com",
            "admin_name": "安全管理员",
            "admin_password": "SecurePass1!",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code == 201, f"Failed to create sample tenant: {resp.text}"
    return resp.json()


# ── Test: 非 platform_admin 无法访问 CRUD 端点 ────────────────────


class TestPlatformAdminOnlyEndpoints:
    """平台 CRUD 只接受独立平台 Cookie，普通 Admin 凭证不进入该认证边界。"""

    @pytest.mark.anyio
    async def test_create_tenant_requires_platform_admin(self, client: AsyncClient):
        """普通 admin 角色不能创建租户。"""
        headers = _tenant_admin_headers("some-tenant-id", "admin")
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "非法租户",
                "plan": "free",
                "admin_email": "bad@test.com",
                "admin_name": "坏人",
                "admin_password": "BadPass123!",
            },
            headers=headers,
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_list_tenants_requires_platform_admin(self, client: AsyncClient):
        """普通 admin 角色不能列出所有租户。"""
        headers = _tenant_admin_headers("some-tenant-id", "admin")
        resp = await client.get("/api/v1/tenants", headers=headers)
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_get_tenant_by_id_requires_platform_admin(self, client: AsyncClient, sample_tenant):
        """普通 admin 角色不能通过 ID 查看任意租户。"""
        headers = _tenant_admin_headers("some-tenant-id", "admin")
        resp = await client.get(f"/api/v1/tenants/{sample_tenant['id']}", headers=headers)
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_update_tenant_by_id_requires_platform_admin(self, client: AsyncClient, sample_tenant):
        """普通 admin 角色不能通过 ID 更新任意租户。"""
        headers = _tenant_admin_headers("some-tenant-id", "admin")
        resp = await client.patch(
            f"/api/v1/tenants/{sample_tenant['id']}",
            json={"name": "被篡改的名字"},
            headers=headers,
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_delete_tenant_requires_platform_admin(self, client: AsyncClient, sample_tenant):
        """普通 admin 角色不能删除租户。"""
        headers = _tenant_admin_headers("some-tenant-id", "admin")
        resp = await client.delete(f"/api/v1/tenants/{sample_tenant['id']}", headers=headers)
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_operator_role_also_denied(self, client: AsyncClient):
        """operator 角色同样不能创建租户。"""
        headers = _tenant_admin_headers("some-tenant-id", "operator")
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "非法租户",
                "plan": "free",
                "admin_email": "bad@test.com",
                "admin_name": "坏人",
                "admin_password": "BadPass123!",
            },
            headers=headers,
        )
        assert resp.status_code == 401


# ── Test: /me 端点字段限制 ────────────────────────────────────────


class TestMeEndpointFieldRestriction:
    """/me PATCH 端点只能修改非敏感字段，敏感字段被 TenantUpdateSelf schema 忽略。"""

    @pytest.mark.anyio
    async def test_me_cannot_change_plan(self, client: AsyncClient, sample_tenant):
        """通过 /me 无法修改 plan — TenantUpdateSelf 不含 plan 字段。"""
        headers = _tenant_admin_headers(sample_tenant["id"])
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"name": "合法修改", "plan": "enterprise"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # plan 不应被改变（TenantUpdateSelf 没有 plan 字段，Pydantic 会忽略它）
        assert data["plan"] == "free"
        assert data["name"] == "合法修改"

    @pytest.mark.anyio
    async def test_me_cannot_change_quota(self, client: AsyncClient, sample_tenant):
        """通过 /me 无法修改 quota — TenantUpdateSelf 不含 quota 字段。"""
        headers = _tenant_admin_headers(sample_tenant["id"])
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"name": "测试", "quota": {"max_codes": 999999}},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # quota 不应被改变
        assert data["quota"]["max_codes"] == 10000

    @pytest.mark.anyio
    async def test_me_cannot_enable_platform_features(self, client: AsyncClient, sample_tenant):
        headers = _tenant_admin_headers(sample_tenant["id"])
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"enabled_features": {"white_label": True}},
            headers=headers,
        )

        assert resp.status_code == 200
        assert not resp.json().get("enabled_features", {}).get("white_label", False)

    @pytest.mark.anyio
    async def test_me_cannot_change_tenant_type(self, client: AsyncClient, sample_tenant):
        """通过 /me 无法修改 tenant_type — TenantUpdateSelf 不含 tenant_type 字段。"""
        headers = _tenant_admin_headers(sample_tenant["id"])
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"name": "测试", "tenant_type": "agency"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_type"] == "brand"

    @pytest.mark.anyio
    async def test_me_cannot_change_compliance_settings(self, client: AsyncClient, sample_tenant):
        """通过 /me 无法修改 compliance_settings。"""
        headers = _tenant_admin_headers(sample_tenant["id"])
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"name": "测试", "compliance_settings": {"gdpr": False}},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # compliance_settings 不应被改变
        assert data.get("compliance_settings") is None or data.get("compliance_settings") == {}

    @pytest.mark.anyio
    async def test_me_allows_non_sensitive_updates(self, client: AsyncClient, sample_tenant):
        """通过 /me 可以合法修改 name、industry、notes。"""
        headers = _tenant_admin_headers(sample_tenant["id"])
        resp = await client.patch(
            "/api/v1/tenants/me",
            json={"name": "新名称", "industry": "食品", "notes": "测试备注"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "新名称"
        assert data["industry"] == "食品"
        assert data["notes"] == "测试备注"


# ── Test: slug 唯一性校验 ──────────────────────────────────────────


class TestSlugUniqueness:
    """Service 层 slug 唯一性校验。"""

    @pytest.mark.anyio
    async def test_duplicate_slug_returns_error(self, client: AsyncClient, sample_tenant):
        """创建租户时使用已存在的 slug 应返回错误。"""
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "另一个租户",
                "slug": sample_tenant["slug"],  # 与 sample_tenant 相同的 slug
                "plan": "free",
                "admin_email": "other@test.com",
                "admin_name": "其他人",
                "admin_password": "OtherPass1!",
            },
            headers=_platform_admin_headers(),
        )
        # ValueError 被端点捕获转为 409 Conflict
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_unique_slug_succeeds(self, client: AsyncClient, sample_tenant):
        """使用不重复的 slug 可以正常创建。"""
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "另一个租户",
                "slug": _unique_slug(),
                "plan": "free",
                "admin_email": "unique@test.com",
                "admin_name": "唯一用户",
                "admin_password": "UniquePass1!",
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201

    @pytest.mark.anyio
    async def test_no_slug_auto_generates(self, client: AsyncClient):
        """不提供 slug 时自动生成，不会冲突。"""
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "自动 slug 租户",
                "plan": "free",
                "admin_email": "autoslug@test.com",
                "admin_name": "自动用户",
                "admin_password": "AutoSlug1!",
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201
        assert resp.json()["slug"]  # 应该有一个自动生成的 slug


# ── Test: 密码强度验证 ─────────────────────────────────────────────


class TestPasswordStrength:
    """TenantCreate 密码强度验证。"""

    @pytest.mark.anyio
    async def test_weak_password_rejected(self, client: AsyncClient):
        """弱密码应被拒绝。"""
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "弱密码租户",
                "plan": "free",
                "admin_email": "weak@test.com",
                "admin_name": "弱密码用户",
                "admin_password": "12345678",  # 纯数字
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_short_password_rejected(self, client: AsyncClient):
        """过短的密码应被拒绝。"""
        resp = await client.post(
            "/api/v1/tenants",
            json={
                "name": "短密码租户",
                "plan": "free",
                "admin_email": "short@test.com",
                "admin_name": "短密码用户",
                "admin_password": "Ab1!",  # 不足 8 位
            },
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 422
