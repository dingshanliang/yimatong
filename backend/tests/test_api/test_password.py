"""A2-007: 密码管理与安全策略验收测试"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.audit import PlatformAuditLog
from app.models.tenant import Account
from app.utils.security import create_access_token, hash_password, verify_password
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession, shared_security_cache):
    # Password routes exercise JWT revocation checks on every request. Keep the
    # focused API suite independent from a developer's local Redis availability.
    del shared_security_cache

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_account(db_session: AsyncSession):
    from uuid6 import uuid7

    from app.models.tenant import Organization, Tenant

    tenant = Tenant(id=uuid7(), name="密码测试", slug=f"pwd-test-{uuid7().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()
    org = Organization(id=uuid7(), tenant_id=tenant.id, name="部门")
    db_session.add(org)
    await db_session.flush()

    account = Account(
        id=uuid7(),
        tenant_id=tenant.id,
        organization_id=org.id,
        email="pwd@test.com",
        hashed_password=hash_password("OldPass12"),
        name="用户",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


def _auth_headers(tenant_id: str, account_id: str) -> dict:
    token = create_access_token(tenant_id, account_id, "admin")
    return {"Authorization": f"Bearer {token}"}


class TestChangePassword:
    @pytest.mark.anyio
    async def test_change_password_success(self, client: AsyncClient, db_session: AsyncSession, seeded_account):
        headers = _auth_headers(str(seeded_account.tenant_id), str(seeded_account.id))
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "OldPass12", "new_password": "NewPass34"},
            headers=headers,
        )
        assert resp.status_code == 200
        set_cookies = resp.headers.get_list("set-cookie")
        assert any("access_token=" in cookie and "Max-Age=0" in cookie for cookie in set_cookies)
        assert any(
            "refresh_token=" in cookie and "Max-Age=0" in cookie and "Path=/api/v1/auth/refresh" in cookie
            for cookie in set_cookies
        )

        # 验证密码确实改了
        result = await db_session.execute(select(Account).where(Account.id == seeded_account.id))
        account = result.scalar_one()
        assert verify_password("NewPass34", account.hashed_password)

    @pytest.mark.anyio
    async def test_change_password_wrong_old(self, client: AsyncClient, seeded_account):
        headers = _auth_headers(str(seeded_account.tenant_id), str(seeded_account.id))
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongPass1", "new_password": "NewPass34"},
            headers=headers,
        )
        assert resp.status_code == 401


class TestResetPassword:
    @pytest.mark.anyio
    async def test_reset_password_by_admin(self, client: AsyncClient, db_session: AsyncSession, seeded_account):
        headers = _auth_headers(str(seeded_account.tenant_id), str(seeded_account.id))
        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"account_id": str(seeded_account.id), "new_password": "ResetPass1", "reason": "用户申请重置"},
            headers=headers,
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(Account).where(Account.id == seeded_account.id))
        account = result.scalar_one()
        assert verify_password("ResetPass1", account.hashed_password)
        assert account.auth_version == 1
        audit = (
            await db_session.execute(
                select(PlatformAuditLog).where(PlatformAuditLog.action == "account_password_reset_by_admin")
            )
        ).scalar_one()
        assert audit.operator_id == str(seeded_account.id)
        assert audit.details["reason"] == "用户申请重置"

    @pytest.mark.anyio
    async def test_reset_password_rolls_back_when_audit_fails(
        self, client: AsyncClient, db_session: AsyncSession, seeded_account, monkeypatch
    ):
        async def fail_audit(*args, **kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("app.api.v1.password.write_audit_log", fail_audit)
        account_id = seeded_account.id
        headers = _auth_headers(str(seeded_account.tenant_id), str(account_id))
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                "/api/v1/auth/reset-password",
                json={
                    "account_id": str(account_id),
                    "new_password": "ResetPass1",
                    "reason": "用户申请重置",
                },
                headers=headers,
            )
        db_session.expire_all()
        account = await db_session.get(Account, account_id)
        assert verify_password("OldPass12", account.hashed_password)
        assert account.auth_version == 0

    @pytest.mark.anyio
    async def test_operator_cannot_generate_reset_token(self, client: AsyncClient, seeded_account):
        headers = {
            "Authorization": (
                "Bearer " + create_access_token(str(seeded_account.tenant_id), str(seeded_account.id), "operator")
            )
        }
        resp = await client.post(
            "/api/v1/auth/generate-reset-token",
            json={"account_id": str(seeded_account.id)},
            headers=headers,
        )

        assert resp.status_code == 403


class TestPasswordStrength:
    @pytest.mark.anyio
    async def test_password_too_short(self, client: AsyncClient, seeded_account):
        headers = _auth_headers(str(seeded_account.tenant_id), str(seeded_account.id))
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "OldPass12", "new_password": "Ab1"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_password_no_digit(self, client: AsyncClient, seeded_account):
        headers = _auth_headers(str(seeded_account.tenant_id), str(seeded_account.id))
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "OldPass12", "new_password": "abcdefgh"},
            headers=headers,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_password_no_letter(self, client: AsyncClient, seeded_account):
        headers = _auth_headers(str(seeded_account.tenant_id), str(seeded_account.id))
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "OldPass12", "new_password": "12345678"},
            headers=headers,
        )
        assert resp.status_code == 422


class TestPasswordHashing:
    def test_bcrypt_cost_factor(self):
        hashed = hash_password("Test1234")
        # bcrypt hash format: $2b$12$... (12 is the cost factor)
        parts = hashed.split("$")
        assert parts[1] == "2b"
        assert int(parts[2]) >= 12

    def test_password_not_in_account_read(self):
        from app.schemas.account import AccountRead

        fields = AccountRead.model_fields
        assert "hashed_password" not in fields
        assert "password" not in fields
