"""A2-006: 平台管理员跨租户操作验收测试"""

import hashlib
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.core.dependencies import get_redis_cache
from app.main import app
from app.models.plan import PlanDefinition
from app.models.platform_opening import PlatformTenantOpening
from app.models.scan import ScanEvent
from app.models.tenant import Account, Organization, Role, Tenant, TenantPlan, TenantStatus, account_roles
from app.services.redis_cache import SharedSecurityCacheUnavailable
from app.utils.security import create_access_token, hash_password
from tests.conftest import TestSessionLocal


class FakePlatformLoginCache:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.block_ip = False
        self.block_account = False
        self.fail_rate_limits = False
        self.revoked_tokens: list[tuple[str, int]] = []

    async def rate_limit_check_shared(self, key: str, max_attempts: int, window_seconds: int) -> tuple[bool, int]:
        assert max_attempts == 10
        assert window_seconds == 300
        if self.fail_rate_limits:
            raise SharedSecurityCacheUnavailable("test shared rate-limit failure")
        self.calls.append(key)
        blocked = (self.block_ip and ":ip:" in key) or (self.block_account and ":account:" in key)
        return (False, 0) if blocked else (True, 9)

    async def revoke_token(self, jti: str, ttl: int) -> None:
        self.revoked_tokens.append((jti, ttl))


@pytest.fixture
def platform_login_cache() -> FakePlatformLoginCache:
    return FakePlatformLoginCache()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession, platform_login_cache: FakePlatformLoginCache, shared_security_cache):
    async def override_get_db():
        yield db_session

    async def override_get_redis_cache():
        return platform_login_cache

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    app.dependency_overrides[get_redis_cache] = override_get_redis_cache
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _platform_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin", tenant_type="platform")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


class TestPlatformAdminAuth:
    @pytest.mark.anyio
    async def test_active_plan_definitions_exclude_inactive_options(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        definitions = (await db_session.execute(select(PlanDefinition))).scalars().all()
        for definition in definitions:
            definition.is_active = definition.name == "starter"
        await db_session.flush()

        response = await client.get("/api/v1/platform/plans/active", headers=_platform_headers())

        assert response.status_code == 200
        assert [(item["name"], item["display_name"]) for item in response.json()] == [("starter", "入门版")]

    @pytest.mark.anyio
    async def test_platform_login_success(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"authenticated": True, "principal": "platform_admin"}
        assert "access_token" not in data
        assert "token" not in data
        platform_cookies = [
            cookie for cookie in resp.headers.get_list("set-cookie") if cookie.startswith("platform_access_token=")
        ]
        assert platform_cookies
        assert "HttpOnly" in platform_cookies[0]

    @pytest.mark.anyio
    async def test_platform_logout_revokes_token_and_clears_cookie(
        self,
        client: AsyncClient,
        platform_login_cache: FakePlatformLoginCache,
    ):
        login = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )
        assert login.status_code == 200
        csrf_token = client.cookies.get("platform_csrf_token")
        assert csrf_token

        response = await client.post(
            "/api/v1/platform/auth/logout",
            headers={"Origin": "http://localhost:3002", "X-Platform-CSRF": csrf_token},
        )

        assert response.status_code == 200
        assert platform_login_cache.revoked_tokens
        assert any(
            "platform_access_token=" in cookie and "Max-Age=0" in cookie
            for cookie in response.headers.get_list("set-cookie")
        )

    @pytest.mark.anyio
    async def test_platform_write_rejects_missing_and_foreign_origin(self, client: AsyncClient):
        headers = _platform_headers()
        missing_origin = {key: value for key, value in headers.items() if key != "Origin"}
        foreign_origin = {**headers, "Origin": "https://evil.example"}

        missing = await client.post(
            "/api/v1/platform/plans",
            json={"name": "free", "display_name": "免费版"},
            headers=missing_origin,
        )
        foreign = await client.post(
            "/api/v1/platform/plans",
            json={"name": "free", "display_name": "免费版"},
            headers=foreign_origin,
        )

        assert missing.status_code == 403
        assert missing.json()["detail"] == "Invalid platform request origin"
        assert foreign.status_code == 403
        assert foreign.json()["detail"] == "Invalid platform request origin"

    @pytest.mark.anyio
    async def test_platform_write_rejects_missing_csrf_header(self, client: AsyncClient):
        headers = _platform_headers()
        headers.pop("X-Platform-CSRF")

        response = await client.post(
            "/api/v1/platform/plans",
            json={"name": "free", "display_name": "免费版"},
            headers=headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid platform CSRF token"

    @pytest.mark.anyio
    async def test_platform_login_invalid_credentials(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "wrong"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_platform_cookie_contains_platform_admin_role_without_exposing_token_in_json(
        self, client: AsyncClient
    ):
        resp = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )
        assert "access_token" not in resp.json()
        assert "token" not in resp.json()
        token = client.cookies.get("platform_access_token")
        assert token
        from app.utils.security import decode_token

        payload = decode_token(token)
        assert payload["role"] == "platform_admin"
        assert payload["tenant_type"] == "platform"

    @pytest.mark.anyio
    async def test_platform_login_fails_closed_when_audit_is_unavailable(self, client: AsyncClient):
        with patch("app.api.v1.platform.write_audit_log", side_effect=RuntimeError("audit unavailable")):
            with pytest.raises(RuntimeError, match="audit unavailable"):
                await client.post(
                    "/api/v1/platform/auth/login",
                    json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
                )

    @pytest.mark.anyio
    async def test_platform_login_ignores_forwarded_headers_and_uses_account_limit(
        self, client: AsyncClient, platform_login_cache: FakePlatformLoginCache
    ):
        for spoofed_ip in ("198.51.100.10", "203.0.113.20"):
            response = await client.post(
                "/api/v1/platform/auth/login",
                json={"email": "platform@yimatong.cn", "password": "wrong"},
                headers={"X-Forwarded-For": spoofed_ip, "X-Real-IP": spoofed_ip},
            )
            assert response.status_code == 401

        keys = platform_login_cache.calls
        ip_keys = {key for key in keys if ":ip:" in key}
        account_keys = {key for key in keys if ":account:" in key}
        assert len(ip_keys) == 1
        expected_account_hash = hashlib.sha256(b"platform@yimatong.cn").hexdigest()
        assert account_keys == {f"platform_login_rate:account:{expected_account_hash}"}
        assert all("198.51.100.10" not in key and "203.0.113.20" not in key for key in keys)
        assert all("platform@yimatong.cn" not in key for key in keys)

    @pytest.mark.anyio
    async def test_platform_login_is_blocked_by_ip_limit(
        self, client: AsyncClient, platform_login_cache: FakePlatformLoginCache
    ):
        platform_login_cache.block_ip = True
        response = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "wrong"},
        )

        assert response.status_code == 429

    @pytest.mark.anyio
    async def test_platform_login_is_blocked_by_account_limit(
        self, client: AsyncClient, platform_login_cache: FakePlatformLoginCache
    ):
        platform_login_cache.block_account = True
        response = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "wrong"},
            headers={"X-Forwarded-For": "198.51.100.99"},
        )

        assert response.status_code == 429

    @pytest.mark.anyio
    async def test_platform_login_fails_closed_when_shared_rate_limit_is_unavailable(
        self, client: AsyncClient, platform_login_cache: FakePlatformLoginCache
    ):
        platform_login_cache.fail_rate_limits = True

        response = await client.post(
            "/api/v1/platform/auth/login",
            json={"email": "platform@yimatong.cn", "password": "platform_admin_2026"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "登录服务暂时不可用，请稍后重试"

    @pytest.mark.anyio
    async def test_tenant_platform_admin_role_cannot_enter_platform_control_plane(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tenant = Tenant(name="普通租户", slug="tenant-platform-role", status=TenantStatus.active)
        db_session.add(tenant)
        await db_session.flush()
        organization = Organization(tenant_id=tenant.id, name="总部")
        role = Role(tenant_id=tenant.id, name="platform_admin", description="历史错误角色")
        db_session.add_all([organization, role])
        await db_session.flush()
        account = Account(
            tenant_id=tenant.id,
            organization_id=organization.id,
            email="legacy-platform-role@example.com",
            hashed_password=hash_password("Password1"),
            name="历史账号",
        )
        db_session.add(account)
        await db_session.flush()
        await db_session.execute(account_roles.insert().values(account_id=account.id, role_id=role.id))

        token = create_access_token(
            str(tenant.id),
            str(account.id),
            "platform_admin",
            tenant_type="brand",
        )
        response = await client.get(
            "/api/v1/platform/dashboard",
            headers={"Cookie": f"platform_access_token={token}"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid platform principal"


class TestPlatformTenantUpdate:
    @pytest.mark.anyio
    async def test_generic_patch_rejects_plan_changes(self, client: AsyncClient, db_session: AsyncSession):
        tenant = Tenant(
            name="套餐边界测试",
            slug="plan-boundary-test",
            status=TenantStatus.active,
            plan=TenantPlan.starter,
            quota={"max_accounts": 5},
            enabled_features={"ai_assistant": True},
        )
        db_session.add(tenant)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/platform/tenants/{tenant.id}",
            json={"plan": "enterprise"},
            headers=_platform_headers(),
        )

        assert response.status_code == 422
        assert tenant.plan == TenantPlan.starter
        assert tenant.quota == {"max_accounts": 5}

    @pytest.mark.anyio
    async def test_termination_cancels_pending_activation_revokes_sessions_and_cannot_be_reversed(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tenant = Tenant(name="终止边界测试", slug="termination-boundary", status=TenantStatus.suspended)
        db_session.add(tenant)
        await db_session.flush()
        organization = Organization(tenant_id=tenant.id, name="总部")
        db_session.add(organization)
        await db_session.flush()
        account = Account(
            tenant_id=tenant.id,
            organization_id=organization.id,
            email="pending-owner@example.com",
            hashed_password=hash_password("Password1"),
            name="待激活管理员",
            is_active=False,
            auth_version=4,
        )
        db_session.add(account)
        await db_session.flush()
        opening = PlatformTenantOpening(
            idempotency_key="termination-boundary-key",
            request_hash="d" * 64,
            tenant_id=tenant.id,
            initial_admin_id=account.id,
            initial_admin_state="pending_activation",
        )
        db_session.add(opening)
        await db_session.flush()

        terminated = await client.patch(
            f"/api/v1/platform/tenants/{tenant.id}/status",
            json={"status": "terminated"},
            headers=_platform_headers(),
        )
        reissue = await client.post(
            f"/api/v1/platform/tenants/{tenant.id}/initial-admin-activation",
            headers=_platform_headers(),
        )
        restore = await client.patch(
            f"/api/v1/platform/tenants/{tenant.id}/status",
            json={"status": "active"},
            headers=_platform_headers(),
        )
        listed = await client.get("/api/v1/platform/tenants", headers=_platform_headers())
        detail = await client.get(f"/api/v1/platform/tenants/{tenant.id}", headers=_platform_headers())

        await db_session.refresh(account)
        await db_session.refresh(opening)
        assert terminated.status_code == 200
        assert account.auth_version == 5
        assert opening.initial_admin_state == "cancelled"
        assert reissue.status_code == 409
        assert restore.status_code == 409
        listed_tenant = next(item for item in listed.json()["items"] if item["id"] == str(tenant.id))
        assert listed_tenant["activation_retryable"] is False
        assert detail.json()["activation_retryable"] is False

    @pytest.mark.anyio
    async def test_assign_plan_updates_entitlements_and_explicitly_clears_expiry(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tenant = Tenant(
            name="套餐原子更新测试",
            slug="plan-atomic-update-test",
            status=TenantStatus.active,
            plan=TenantPlan.starter,
            plan_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            quota={"max_accounts": 5},
            enabled_features={"ai_assistant": True},
        )
        db_session.add(tenant)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/platform/tenants/{tenant.id}/assign-plan",
            json={"plan_id": "test-plan-enterprise", "expires_on": None},
            headers=_platform_headers(),
        )

        assert response.status_code == 200
        assert tenant.plan == TenantPlan.enterprise
        assert tenant.quota == {"max_codes": -1, "max_campaigns": -1, "max_accounts": -1}
        assert tenant.enabled_features == {"ai_assistant": True, "risk_module": True, "white_label": True}
        assert tenant.plan_expires_at is None

    @pytest.mark.anyio
    async def test_assign_plan_uses_inclusive_china_business_date_without_client_timezone_drift(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tenant = Tenant(name="日期边界测试", slug="plan-date-boundary", status=TenantStatus.active)
        db_session.add(tenant)
        await db_session.flush()

        response = await client.post(
            f"/api/v1/platform/tenants/{tenant.id}/assign-plan",
            json={"plan_id": "test-plan-enterprise", "expires_on": "2027-07-01"},
            headers=_platform_headers(),
        )

        assert response.status_code == 200
        assert tenant.plan_expires_at == datetime(2027, 7, 1, 15, 59, 59, 999999, tzinfo=UTC)
        assert response.json()["plan_expires_at"] == "2027-07-01T15:59:59.999999Z"

    @pytest.mark.anyio
    async def test_platform_control_plane_rejects_bearer_even_for_valid_platform_principal(self, client: AsyncClient):
        token = create_access_token("platform", "platform-admin", "platform_admin", tenant_type="platform")

        response = await client.get(
            "/api/v1/platform/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing platform session"

    @pytest.mark.anyio
    async def test_platform_control_plane_does_not_accept_admin_cookie(self, client: AsyncClient):
        token = create_access_token("platform", "platform-admin", "platform_admin", tenant_type="platform")

        response = await client.get(
            "/api/v1/platform/dashboard",
            headers={"Cookie": f"access_token={token}"},
        )

        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_unknown_plan_definition_is_rejected_without_entitlement_drift(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tenant = Tenant(
            name="未知套餐边界",
            slug="unknown-plan-boundary",
            status=TenantStatus.active,
            plan=TenantPlan.starter,
            quota={"max_accounts": 5},
            enabled_features={"ai_assistant": False},
        )
        invalid_plan = PlanDefinition(
            id="legacy-custom-plan",
            name="legacy_custom",
            display_name="历史自定义套餐",
            quota_defaults={"max_accounts": 999},
            feature_flags={"ai_assistant": True},
        )
        db_session.add_all([tenant, invalid_plan])
        await db_session.flush()

        response = await client.post(
            f"/api/v1/platform/tenants/{tenant.id}/assign-plan",
            json={"plan_id": invalid_plan.id},
            headers=_platform_headers(),
        )

        assert response.status_code == 409
        assert tenant.plan == TenantPlan.starter
        assert tenant.quota == {"max_accounts": 5}
        assert tenant.enabled_features == {"ai_assistant": False}

    @pytest.mark.anyio
    async def test_plan_creation_rejects_unsupported_name(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/platform/plans",
            json={"name": "legacy_custom", "display_name": "不支持套餐"},
            headers=_platform_headers(),
        )

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_quota_usage_counts_authoritative_scan_events_per_tenant(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        tenant = Tenant(name="扫码额度测试", slug="scan-quota-usage", status=TenantStatus.active)
        other = Tenant(name="其他租户", slug="scan-quota-other", status=TenantStatus.active)
        db_session.add_all([tenant, other])
        await db_session.flush()
        db_session.add_all(
            [
                ScanEvent(
                    tenant_id=tenant.id,
                    public_id=f"SCAN{index}",
                    scan_time=datetime.now(UTC),
                    id=uuid.uuid4(),
                )
                for index in range(2)
            ]
            + [
                ScanEvent(
                    tenant_id=other.id,
                    public_id="OTHER",
                    scan_time=datetime.now(UTC),
                    id=uuid.uuid4(),
                )
            ]
        )
        await db_session.flush()

        response = await client.get("/api/v1/platform/quota-usage", headers=_platform_headers())

        assert response.status_code == 200
        item = next(row for row in response.json() if row["tenant_id"] == str(tenant.id))
        assert item["usage"]["scans"] == 2


class TestPlatformAuditLogs:
    @pytest.mark.anyio
    async def test_list_audit_logs(self, client: AsyncClient, db_session: AsyncSession):
        from app.services.audit import write_audit_log

        await write_audit_log(db_session, "admin-1", "tenant-1", "read", "tenants/123")
        resp = await client.get("/api/v1/platform/audit-logs", headers=_platform_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["operator_id"] == "admin-1"
        assert data[0]["action"] == "read"

    @pytest.mark.anyio
    async def test_audit_logs_require_platform_admin(self, client: AsyncClient):
        regular_token = create_access_token("tenant-1", "user-1", "admin")
        headers = {"Cookie": f"platform_access_token={regular_token}"}
        resp = await client.get("/api/v1/platform/audit-logs", headers=headers)
        assert resp.status_code == 403
