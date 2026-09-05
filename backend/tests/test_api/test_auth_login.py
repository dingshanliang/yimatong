"""A2-003: 账号登录与 token 颁发验收测试"""

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.tenant import Account, Role, Tenant, TenantStatus
from app.services.auth import resolve_account_role
from app.utils.auth_rbac import WEB_ROLE_PERMISSIONS
from app.utils.security import create_access_token, decode_token, hash_password
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession, shared_security_cache):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_account(db_session: AsyncSession):
    """创建一个可登录的测试账号（含租户+组织）"""
    from uuid6 import uuid7

    from app.models.tenant import Organization, Tenant

    tenant = Tenant(id=uuid7(), name="Login测试租户", slug=f"login-test-{uuid7().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()

    org = Organization(id=uuid7(), tenant_id=tenant.id, name="测试部门")
    db_session.add(org)
    await db_session.flush()

    account = Account(
        id=uuid7(),
        tenant_id=tenant.id,
        organization_id=org.id,
        email="login@test.com",
        hashed_password=hash_password("Password1"),
        name="测试用户",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


class TestLogin:
    @pytest.mark.parametrize("role_name", ["distributor", "store_guide"])
    def test_channel_portal_roles_are_canonical_but_have_no_generic_permissions(self, role_name: str):
        account = Account(roles=[Role(name=role_name)])

        assert resolve_account_role(account) == role_name
        assert WEB_ROLE_PERMISSIONS[role_name] == []

    @pytest.mark.anyio
    async def test_login_success_returns_tokens(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.anyio
    async def test_browser_login_keeps_refresh_token_httponly_only(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
            headers={"X-Auth-Delivery": "cookie"},
        )

        assert resp.status_code == 200
        assert "refresh_token" not in resp.json()
        refresh_cookies = [
            cookie for cookie in resp.headers.get_list("set-cookie") if cookie.startswith("refresh_token=")
        ]
        assert len(refresh_cookies) == 1
        assert "HttpOnly" in refresh_cookies[0]
        assert "Path=/api/v1/auth" in refresh_cookies[0]

    @pytest.mark.anyio
    async def test_login_email_identity_is_case_and_whitespace_insensitive(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "  LOGIN@TEST.COM  ", "password": "Password1"},
        )

        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_login_uses_assigned_account_role(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        role = Role(tenant_id=seeded_account.tenant_id, name="operator", description="运营")
        db_session.add(role)
        await db_session.flush()
        seeded_account.roles = [role]
        await db_session.commit()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )

        assert resp.status_code == 200
        payload = decode_token(resp.json()["access_token"])
        assert payload["role"] == "operator"

    @pytest.mark.anyio
    @pytest.mark.parametrize("role_name", ["distributor", "store_guide"])
    async def test_login_preserves_channel_portal_role(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account, role_name: str
    ):
        role = Role(tenant_id=seeded_account.tenant_id, name=role_name, description="渠道门户")
        db_session.add(role)
        await db_session.flush()
        seeded_account.roles = [role]
        await db_session.commit()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )

        assert resp.status_code == 200
        payload = decode_token(resp.json()["access_token"])
        assert payload["role"] == role_name

    @pytest.mark.anyio
    async def test_login_can_target_tenant_slug_when_email_is_duplicated(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        from uuid6 import uuid7

        from app.models.tenant import Organization, Tenant

        demo_tenant = Tenant(id=uuid7(), name="演示租户", slug="demo")
        db_session.add(demo_tenant)
        await db_session.flush()

        demo_org = Organization(id=uuid7(), tenant_id=demo_tenant.id, name="演示部门")
        db_session.add(demo_org)
        await db_session.flush()

        demo_account = Account(
            id=uuid7(),
            tenant_id=demo_tenant.id,
            organization_id=demo_org.id,
            email=seeded_account.email,
            hashed_password=hash_password("DemoPassword1"),
            name="演示用户",
        )
        db_session.add(demo_account)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": seeded_account.email, "password": "DemoPassword1", "tenant_slug": "demo"},
        )

        assert resp.status_code == 200
        payload = decode_token(resp.json()["access_token"])
        assert payload["tenant_id"] == str(demo_tenant.id)
        assert payload["sub"] == str(demo_account.id)

    @pytest.mark.anyio
    async def test_login_requires_tenant_slug_when_email_is_duplicated(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        from uuid6 import uuid7

        from app.models.tenant import Organization, Tenant

        other_tenant = Tenant(id=uuid7(), name="另一工作区", slug=f"other-{uuid7().hex[:8]}")
        db_session.add(other_tenant)
        await db_session.flush()
        other_org = Organization(id=uuid7(), tenant_id=other_tenant.id, name="另一部门")
        db_session.add(other_org)
        await db_session.flush()
        db_session.add(
            Account(
                id=uuid7(),
                tenant_id=other_tenant.id,
                organization_id=other_org.id,
                email=seeded_account.email,
                hashed_password=hash_password("OtherPassword1"),
                name="另一用户",
            )
        )
        await db_session.commit()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": seeded_account.email, "password": "Password1"},
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "该邮箱关联多个工作区，请填写工作区标识"

    @pytest.mark.anyio
    async def test_duplicate_email_wrong_password_does_not_reveal_multiple_workspaces(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        from uuid6 import uuid7

        from app.models.tenant import Organization, Tenant

        other_tenant = Tenant(id=uuid7(), name="另一工作区", slug=f"other-{uuid7().hex[:8]}")
        db_session.add(other_tenant)
        await db_session.flush()
        other_org = Organization(id=uuid7(), tenant_id=other_tenant.id, name="另一部门")
        db_session.add(other_org)
        await db_session.flush()
        db_session.add(
            Account(
                id=uuid7(),
                tenant_id=other_tenant.id,
                organization_id=other_org.id,
                email=seeded_account.email,
                hashed_password=hash_password("OtherPassword1"),
                name="另一用户",
            )
        )
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": seeded_account.email, "password": "CompletelyWrong1"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "邮箱或密码不正确"

    @pytest.mark.anyio
    async def test_login_failure_returns_401(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "wrong1"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "邮箱或密码不正确"

    @pytest.mark.anyio
    async def test_login_nonexistent_email_returns_401(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.com", "password": "Password1"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "邮箱或密码不正确"

    @pytest.mark.anyio
    async def test_disabled_account_cannot_login(self, client: AsyncClient, db_session: AsyncSession, seeded_account):
        seeded_account.is_active = False
        seeded_account.auth_version += 1
        await db_session.commit()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "账户已停用，请联系租户管理员"

    @pytest.mark.anyio
    async def test_suspended_tenant_cannot_login_or_refresh(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        refresh_token = login_resp.json()["refresh_token"]
        tenant = await db_session.get(Tenant, seeded_account.tenant_id)
        tenant.status = TenantStatus.suspended
        await db_session.commit()

        blocked_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        blocked_refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert blocked_login.status_code == 403
        assert blocked_refresh.status_code == 401

    @pytest.mark.anyio
    async def test_login_success_records_last_login_time(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        result = await db_session.execute(select(Account).where(Account.id == seeded_account.id))
        account = result.scalar_one()
        assert account.last_login_at is not None
        assert account.last_login_at is not None

    @pytest.mark.anyio
    async def test_login_success_resets_failed_attempts(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        # 先失败几次
        for _ in range(3):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "login@test.com", "password": "wrong1"},
            )
        # 再成功
        await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        result = await db_session.execute(select(Account).where(Account.id == seeded_account.id))
        account = result.scalar_one()
        assert account.failed_login_attempts == 0


class TestAccountLocking:
    @pytest.mark.anyio
    async def test_account_locked_after_5_failures(self, client: AsyncClient, db_session: AsyncSession, seeded_account):
        unique_ip = f"10.0.{id(self) % 255}.{(_ := id(self) // 255) % 255}"
        headers = {"X-Forwarded-For": unique_ip}
        for _ in range(5):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "login@test.com", "password": "wrong1"},
                headers=headers,
            )
            assert resp.status_code == 401

        # 第6次用正确密码也应被拒（锁定中），但提示可区分且有出路
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
            headers=headers,
        )
        assert resp.status_code == 423
        assert "锁定" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_locked_account_has_locked_until_set(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        unique_ip = f"10.1.{id(self) % 255}.{(_ := id(self) // 255) % 255}"
        headers = {"X-Forwarded-For": unique_ip}
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "login@test.com", "password": "wrong1"},
                headers=headers,
            )
        result = await db_session.execute(select(Account).where(Account.id == seeded_account.id))
        account = result.scalar_one()
        assert account.locked_until is not None
        assert account.failed_login_attempts == 5


class TestTokenRefresh:
    @pytest.mark.anyio
    async def test_refresh_returns_new_tokens(self, client: AsyncClient, seeded_account):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.anyio
    async def test_browser_refresh_rotates_httponly_cookie_without_json_token(
        self, client: AsyncClient, seeded_account
    ):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
            headers={"X-Auth-Delivery": "cookie"},
        )
        previous_refresh = client.cookies.get("refresh_token")

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={},
        )

        assert login_resp.status_code == 200
        assert resp.status_code == 200
        assert "refresh_token" not in resp.json()
        assert client.cookies.get("refresh_token") != previous_refresh
        assert any(
            cookie.startswith("refresh_token=") and "HttpOnly" in cookie and "Path=/api/v1/auth" in cookie
            for cookie in resp.headers.get_list("set-cookie")
        )

    @pytest.mark.anyio
    async def test_logout_with_expired_access_still_revokes_cookie_session(self, client: AsyncClient, seeded_account):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
            headers={"X-Auth-Delivery": "cookie"},
        )
        refresh_token = client.cookies.get("refresh_token")
        with patch("app.utils.security.settings.access_token_expire_minutes", -1):
            expired_access = create_access_token(
                str(seeded_account.tenant_id),
                str(seeded_account.id),
                "admin",
            )

        logout_resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {expired_access}"},
        )
        replay_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert login_resp.status_code == 200
        assert logout_resp.status_code == 200
        assert replay_resp.status_code == 401

    @pytest.mark.anyio
    async def test_refresh_body_takes_precedence_over_cookie(self, client: AsyncClient, seeded_account):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        client.cookies.set("refresh_token", "invalid-token")
        try:
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh_token},
            )
        finally:
            client.cookies.delete("refresh_token")

        assert resp.status_code == 200
        assert "access_token" in resp.json()

    @pytest.mark.anyio
    async def test_refresh_with_invalid_token_returns_401(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_status_change_revokes_refresh_and_existing_access_tokens(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        access_token = login_resp.json()["access_token"]
        refresh_token = login_resp.json()["refresh_token"]

        seeded_account.is_active = False
        seeded_account.auth_version += 1
        await db_session.commit()

        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        with patch("app.core.database.async_session_factory", TestSessionLocal):
            protected_resp = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert refresh_resp.status_code == 401
        assert protected_resp.status_code == 401

        seeded_account.is_active = True
        seeded_account.auth_version += 1
        await db_session.commit()

        with patch("app.core.database.async_session_factory", TestSessionLocal):
            restored_old_token_resp = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert restored_old_token_resp.status_code == 401
