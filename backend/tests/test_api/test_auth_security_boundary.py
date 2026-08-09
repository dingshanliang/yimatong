"""Auth 安全边界测试 — IP 速率限制、JWT 黑名单、refresh 轮换、账户锁定。"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.auth_security import AuthSession
from app.models.tenant import Account
from app.utils.security import decode_token, hash_password


@pytest.fixture
async def client(db: AsyncSession, shared_security_cache):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_account(db: AsyncSession):
    from uuid6 import uuid7

    from app.models.tenant import Organization, Tenant

    tenant = Tenant(id=uuid7(), name="Security测试租户", slug=f"sec-test-{uuid7().hex[:8]}")
    db.add(tenant)
    await db.flush()

    org = Organization(id=uuid7(), tenant_id=tenant.id, name="测试部门")
    db.add(org)
    await db.flush()

    account = Account(
        id=uuid7(),
        tenant_id=tenant.id,
        organization_id=org.id,
        email="security@test.com",
        hashed_password=hash_password("Password1"),
        name="安全测试用户",
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


class TestIPRateLimit:
    @pytest.mark.anyio
    async def test_login_rate_limit_returns_429(self, client: AsyncClient, seeded_account):
        """超过每 IP 20 次/分钟限制后返回 429。"""
        headers = {"X-Forwarded-For": "99.99.99.99"}
        for _ in range(20):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "security@test.com", "password": "wrong1"},
                headers=headers,
            )
        # 第 21 次应该被限制
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "wrong1"},
            headers=headers,
        )
        assert resp.status_code == 429

    @pytest.mark.anyio
    async def test_forwarded_header_cannot_bypass_client_ip_limit(self, client: AsyncClient, seeded_account):
        """未受信的 X-Forwarded-For 不得改变请求来源或绕过共享限制。"""
        headers_a = {"X-Forwarded-For": "88.88.88.88"}
        headers_b = {"X-Forwarded-For": "77.77.77.77"}
        for _ in range(20):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "security@test.com", "password": "wrong1"},
                headers=headers_a,
            )
        # 换一个邮箱排除邮箱维度限制；伪造 XFF 后仍命中同一个 request.client 限制。
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody-else@test.com", "password": "wrong1"},
            headers=headers_b,
        )
        assert resp.status_code == 429

    @pytest.mark.anyio
    async def test_login_rate_limit_uses_hashed_email_key(
        self, client: AsyncClient, seeded_account, shared_security_cache
    ):
        await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "wrong1"},
        )

        email_keys = [key for key in shared_security_cache.rate_keys if key.startswith("login:email:")]
        assert len(email_keys) == 1
        assert "security@test.com" not in email_keys[0]

    @pytest.mark.anyio
    async def test_login_fails_closed_when_shared_rate_limit_is_unavailable(
        self, client: AsyncClient, seeded_account, shared_security_cache
    ):
        shared_security_cache.fail_rate_limits = True

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )

        assert response.status_code == 503


class TestJWTBlacklist:
    @pytest.mark.anyio
    async def test_successful_login_prunes_a_bounded_expired_session(
        self, client: AsyncClient, db: AsyncSession, seeded_account
    ):
        expired_session_id = uuid.uuid4()
        db.add(
            AuthSession(
                id=expired_session_id,
                account_id=seeded_account.id,
                tenant_id=seeded_account.tenant_id,
                auth_version=seeded_account.auth_version,
                current_refresh_jti=str(uuid.uuid4()),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await db.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )

        assert response.status_code == 200
        assert await db.get(AuthSession, expired_session_id) is None

    @pytest.mark.anyio
    async def test_logout_globally_revokes_access_and_refresh(
        self, client: AsyncClient, db: AsyncSession, seeded_account, shared_security_cache
    ):
        """登出后 access token 被加入黑名单，再次使用 /me 返回 401。"""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        refresh_token = login_resp.json()["refresh_token"]
        refresh_payload = decode_token(refresh_token)
        session_id = uuid.UUID(refresh_payload["sid"])
        access_jti = decode_token(token)["jti"]

        # 登出
        logout_resp = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_resp.status_code == 200
        auth_session = await db.get(AuthSession, session_id)
        assert auth_session is not None
        assert auth_session.revoked_at is not None
        assert access_jti in shared_security_cache.revoked_jtis

        # 用被黑名单的 token 访问 /me 应该失败
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 401

        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_resp.status_code == 401

    @pytest.mark.anyio
    async def test_authenticated_request_fails_closed_when_revocation_read_is_unavailable(
        self, client: AsyncClient, seeded_account, shared_security_cache
    ):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        shared_security_cache.fail_reads = True

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login_resp.json()['access_token']}"},
        )

        assert response.status_code == 503

    @pytest.mark.anyio
    async def test_logout_fails_when_access_revocation_write_is_unavailable_but_persists_refresh(
        self, client: AsyncClient, db: AsyncSession, seeded_account, shared_security_cache
    ):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        token_pair = login_resp.json()
        session_id = uuid.UUID(decode_token(token_pair["refresh_token"])["sid"])
        shared_security_cache.fail_writes = True

        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": token_pair["refresh_token"]},
            headers={"Authorization": f"Bearer {token_pair['access_token']}"},
        )

        assert response.status_code == 503
        assert response.json()["code"] == "LOGOUT_PARTIAL"
        set_cookies = response.headers.get_list("set-cookie")
        assert any(
            "refresh_token=" in cookie and "Max-Age=0" in cookie and "Path=/api/v1/auth" in cookie
            for cookie in set_cookies
        )
        assert any(
            "refresh_token=" in cookie and "Max-Age=0" in cookie and "Path=/api/v1/auth/refresh" in cookie
            for cookie in set_cookies
        )
        auth_session = await db.get(AuthSession, session_id)
        assert auth_session is not None
        assert auth_session.revoked_at is not None

    @pytest.mark.anyio
    async def test_logout_revokes_explicit_refresh_token_and_stale_cookie(self, client: AsyncClient, seeded_account):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        access_token = login_resp.json()["access_token"]
        current_refresh = login_resp.json()["refresh_token"]
        client.cookies.set("refresh_token", "stale-cookie-token", path="/")

        with patch("app.api.v1.auth.logout_session", new_callable=AsyncMock) as logout_session:
            response = await client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": current_refresh},
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == 200
        assert logout_session.await_args.kwargs["refresh_token_str"] == "stale-cookie-token"
        assert logout_session.await_args.kwargs["additional_refresh_token_str"] == current_refresh
        assert logout_session.await_args.kwargs["db"] is not None

    @pytest.mark.anyio
    async def test_logout_revokes_mismatched_body_and_cookie_sessions(self, client: AsyncClient, seeded_account):
        first_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        first_refresh = first_login.json()["refresh_token"]
        second_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        second_pair = second_login.json()

        logout = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": first_refresh},
            headers={"Authorization": f"Bearer {second_pair['access_token']}"},
        )
        first_replay = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": first_refresh},
        )
        second_replay = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": second_pair["refresh_token"]},
        )

        assert logout.status_code == 200
        assert first_replay.status_code == 401
        assert second_replay.status_code == 401

    @pytest.mark.anyio
    async def test_logout_with_legacy_old_body_revokes_the_rotated_cookie_family(
        self, client: AsyncClient, seeded_account
    ):
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        old_refresh = login.json()["refresh_token"]
        rotated = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh},
        )

        logout = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": old_refresh},
            headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
        )
        descendant = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": rotated.json()["refresh_token"]},
        )

        assert rotated.status_code == 200
        assert logout.status_code == 200
        assert descendant.status_code == 401


class TestRefreshTokenRotation:
    @pytest.mark.anyio
    async def test_refresh_returns_new_tokens(self, client: AsyncClient, seeded_account):
        """刷新 token 返回不同的 access_token 和 refresh_token。"""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        old_access = login_resp.json()["access_token"]
        old_refresh = login_resp.json()["refresh_token"]

        # 刷新
        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh},
        )
        assert refresh_resp.status_code == 200
        new_access = refresh_resp.json()["access_token"]
        new_refresh = refresh_resp.json()["refresh_token"]

        # 新 token 应该不同于旧 token
        assert new_access != old_access
        assert new_refresh != old_refresh

    @pytest.mark.anyio
    async def test_refresh_with_invalid_token_returns_401(self, client: AsyncClient, seeded_account):
        """无效 refresh token 返回 401。"""
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid_token_string"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_refresh_token_can_only_be_consumed_once(self, client: AsyncClient, seeded_account):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        old_refresh = login_resp.json()["refresh_token"]

        first = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})

        assert first.status_code == 200
        assert replay.status_code == 401
        descendant_refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": first.json()["refresh_token"]},
        )
        descendant_access = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {first.json()['access_token']}"},
        )
        assert descendant_refresh.status_code == 401
        assert descendant_access.status_code == 401


class TestAccountLockingExtended:
    @pytest.mark.anyio
    async def test_lock_expires_after_duration(self, client: AsyncClient, db: AsyncSession, seeded_account):
        """账户锁定后 locked_until 过期可以重新登录。"""

        headers = {"X-Forwarded-For": "66.66.66.66"}

        # 触发锁定
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "security@test.com", "password": "wrong1"},
                headers=headers,
            )

        # 确认已锁定
        result = await db.execute(select(Account).where(Account.id == seeded_account.id))
        account = result.scalar_one()
        assert account.locked_until is not None

        # 模拟时间过了锁定周期 — 直接清除 locked_until
        account.locked_until = None
        account.failed_login_attempts = 0
        await db.commit()

        # 用正确密码登录应该成功
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
            headers=headers,
        )
        assert resp.status_code == 200
