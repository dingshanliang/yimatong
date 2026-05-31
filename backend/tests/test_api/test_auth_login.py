"""A2-003: 账号登录与 token 颁发验收测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.tenant import Account, Role
from app.utils.security import decode_token, hash_password
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
    async def test_login_uses_assigned_account_role(self, client: AsyncClient, db_session: AsyncSession, seeded_account):
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
    async def test_login_failure_returns_401(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "wrong"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    @pytest.mark.anyio
    async def test_login_nonexistent_email_returns_401(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.com", "password": "Password1"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

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
                json={"email": "login@test.com", "password": "wrong"},
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
        for _ in range(5):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "login@test.com", "password": "wrong"},
            )
            assert resp.status_code == 401

        # 第6次用正确密码也应该失败（锁定中）
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "Password1"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_locked_account_has_locked_until_set(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account
    ):
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "login@test.com", "password": "wrong"},
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
    async def test_refresh_with_invalid_token_returns_401(self, client: AsyncClient, seeded_account):
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid-token"},
        )
        assert resp.status_code == 401
