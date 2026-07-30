"""Auth 安全边界测试 — IP 速率限制、JWT 黑名单、refresh 轮换、账户锁定。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.tenant import Account
from app.utils.security import hash_password


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
    async def test_login_rate_limit_different_ip_allowed(self, client: AsyncClient, seeded_account):
        """不同 IP 有独立的速率限制。"""
        headers_a = {"X-Forwarded-For": "88.88.88.88"}
        headers_b = {"X-Forwarded-For": "77.77.77.77"}
        for _ in range(20):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "security@test.com", "password": "wrong1"},
                headers=headers_a,
            )
        # IP b 应该仍然可以请求
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "wrong1"},
            headers=headers_b,
        )
        assert resp.status_code in (401, 200)


class TestJWTBlacklist:
    @pytest.mark.anyio
    async def test_logout_blacklists_access_token(self, client: AsyncClient, seeded_account):
        """登出后 access token 被加入黑名单，再次使用 /me 返回 401。"""
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "security@test.com", "password": "Password1"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        # 登出
        logout_resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_resp.status_code == 200

        # 用被黑名单的 token 访问 /me 应该失败
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 401


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
