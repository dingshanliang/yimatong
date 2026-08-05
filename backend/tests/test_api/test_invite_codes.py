"""邀请码系统测试"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_with_bypass
from app.main import app
from app.models.invite_code import TenantInviteCode
from app.models.invite_registration import InviteRegistrationReceipt
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    token = create_access_token("platform", "platform-admin", "platform_admin", tenant_type="platform")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


def _registration_headers(key: str | None = None) -> dict[str, str]:
    return {"Idempotency-Key": key or str(uuid.uuid4())}


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class TestInviteCodeCRUD:
    @pytest.mark.anyio
    async def test_create_invite_code(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 5, "expires_in_days": 7},
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["code"]
        assert data["tenant_type"] == "brand"
        assert data["max_uses"] == 5
        assert data["used_count"] == 0
        assert data["status"] == "active"
        assert data["registration_url"] == f"http://localhost:3000/register?invite_code={data['code']}"

    @pytest.mark.anyio
    async def test_list_invite_codes(self, client: AsyncClient):
        await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "agency", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        resp = await client.get(
            "/api/v1/invite-codes",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1
        assert all(item["registration_url"].startswith("http://localhost:3000/register?") for item in data["items"])

    @pytest.mark.anyio
    async def test_deactivate_invite_code(self, client: AsyncClient):
        create_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        code_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/invite-codes/{code_id}/status?active=false",
            headers=_platform_admin_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"


class TestInviteCodeRegistration:
    @pytest.mark.anyio
    async def test_register_with_valid_invite_code(self, client: AsyncClient):
        # Create invite code
        invite_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        code = invite_resp.json()["code"]

        # Register with invite code
        resp = await client.post(
            "/api/v1/invite-codes/register",
            headers=_registration_headers(),
            json={
                "invite_code": code,
                "name": " invited Tenant",
                "admin_email": "invited@test.com",
                "admin_name": "Invited",
                "admin_password": "Pass1234",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["tenant_id"]
        assert data["tenant_slug"]
        assert "注册成功" in data["message"]

    @pytest.mark.anyio
    async def test_register_with_invalid_invite_code(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/invite-codes/register",
            headers=_registration_headers(),
            json={
                "invite_code": "INVALIDCODE",
                "name": "Bad Tenant",
                "admin_email": "bad@test.com",
                "admin_name": "Bad",
                "admin_password": "Pass1234",
            },
        )
        assert resp.status_code == 400
        assert "Invalid" in resp.json()["detail"] or "邀请码" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_invite_code_depletes_after_use(self, client: AsyncClient):
        # Create invite code with max_uses=1
        invite_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 1},
            headers=_platform_admin_headers(),
        )
        code = invite_resp.json()["code"]

        # First registration succeeds
        resp1 = await client.post(
            "/api/v1/invite-codes/register",
            headers=_registration_headers(),
            json={
                "invite_code": code,
                "name": "First Tenant",
                "admin_email": "first@test.com",
                "admin_name": "First",
                "admin_password": "Pass1234",
            },
        )
        assert resp1.status_code == 201, resp1.text

        # Second registration fails (code depleted)
        resp2 = await client.post(
            "/api/v1/invite-codes/register",
            headers=_registration_headers(),
            json={
                "invite_code": code,
                "name": "Second Tenant",
                "admin_email": "second@test.com",
                "admin_name": "Second",
                "admin_password": "Pass1234",
            },
        )
        assert resp2.status_code == 400

    @pytest.mark.anyio
    async def test_same_key_same_request_replays_completed_receipt_without_consuming_again(
        self, client: AsyncClient, db_session: AsyncSession, shared_security_cache
    ):
        invite_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 2},
            headers=_platform_admin_headers(),
        )
        code = invite_resp.json()["code"]
        payload = {
            "invite_code": code,
            "name": "Replay Tenant",
            "admin_email": "replay@test.com",
            "admin_name": "Replay",
            "admin_password": "Pass1234",
        }
        headers = _registration_headers("registration-replay-key-0001")

        first = await client.post("/api/v1/invite-codes/register", json=payload, headers=headers)
        second = await client.post("/api/v1/invite-codes/register", json=payload, headers=headers)

        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        invite = (await db_session.execute(select(TenantInviteCode).where(TenantInviteCode.code == code))).scalar_one()
        assert invite.used_count == 1
        receipt = (await db_session.execute(select(InviteRegistrationReceipt))).scalar_one()
        assert receipt.tenant_id is not None
        assert receipt.tenant_slug == first.json()["tenant_slug"]
        assert not hasattr(receipt, "admin_email")
        assert not hasattr(receipt, "admin_password")
        invite_rate_keys = [key for key in shared_security_cache.rate_keys if ":invite:" in key]
        assert len(invite_rate_keys) == 1

    @pytest.mark.anyio
    async def test_same_key_different_request_returns_conflict(self, client: AsyncClient):
        invite_resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand", "max_uses": 2},
            headers=_platform_admin_headers(),
        )
        code = invite_resp.json()["code"]
        payload = {
            "invite_code": code,
            "name": "Conflict Tenant",
            "admin_email": "conflict@test.com",
            "admin_name": "Conflict",
            "admin_password": "Pass1234",
        }
        headers = _registration_headers("registration-conflict-key-01")
        first = await client.post("/api/v1/invite-codes/register", json=payload, headers=headers)
        changed = await client.post(
            "/api/v1/invite-codes/register",
            json={**payload, "name": "Different Tenant"},
            headers=headers,
        )

        assert first.status_code == 201
        assert changed.status_code == 409
        assert "同一幂等键" in changed.json()["detail"]


class TestInviteCodeSecurity:
    @staticmethod
    def _invalid_registration_payload(invite_code: str) -> dict[str, str]:
        return {
            "invite_code": invite_code,
            "name": "Rate Limited Tenant",
            "admin_email": "rate-limit@test.com",
            "admin_name": "Rate Limit",
            "admin_password": "Pass1234",
        }

    @pytest.mark.anyio
    async def test_registration_ip_limit_is_shared_across_cache_instances(
        self, client: AsyncClient, shared_security_cache
    ):
        for attempt in range(10):
            response = await client.post(
                "/api/v1/invite-codes/register",
                headers=_registration_headers(),
                json=self._invalid_registration_payload(f"INVALID-{attempt}"),
            )
            assert response.status_code == 400

        with patch("app.api.v1.invite_codes.register_tenant_with_invite") as register:
            blocked = await client.post(
                "/api/v1/invite-codes/register",
                headers=_registration_headers(),
                json=self._invalid_registration_payload("INVALID-BLOCKED"),
            )
        register.assert_not_awaited()

        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "300"
        ip_keys = [key for key in shared_security_cache.rate_keys if ":ip:" in key]
        assert len(set(ip_keys)) == 1
        assert shared_security_cache.rate_counts[ip_keys[0]] == 11

    @pytest.mark.anyio
    async def test_registration_invite_limit_uses_non_reversible_code_digest(
        self, client: AsyncClient, shared_security_cache
    ):
        invite_code = "SENSITIVE-INVITE-CODE"
        for _ in range(5):
            response = await client.post(
                "/api/v1/invite-codes/register",
                headers=_registration_headers(),
                json=self._invalid_registration_payload(invite_code),
            )
            assert response.status_code == 400

        with patch("app.api.v1.invite_codes.register_tenant_with_invite") as register:
            blocked = await client.post(
                "/api/v1/invite-codes/register",
                headers=_registration_headers(),
                json=self._invalid_registration_payload(invite_code),
            )
        register.assert_not_awaited()

        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "300"
        invite_keys = [key for key in shared_security_cache.rate_keys if ":invite:" in key]
        assert len(set(invite_keys)) == 1
        assert all(invite_code not in key for key in shared_security_cache.rate_keys)

    @pytest.mark.anyio
    async def test_registration_ignores_untrusted_forwarded_ip_headers(
        self, client: AsyncClient, shared_security_cache
    ):
        for forwarded_ip in ("not-an-ip", "203.0.113.77, invalid"):
            response = await client.post(
                "/api/v1/invite-codes/register",
                headers={**_registration_headers(), "X-Forwarded-For": forwarded_ip, "X-Real-IP": forwarded_ip},
                json=self._invalid_registration_payload(f"INVALID-{uuid.uuid4()}"),
            )
            assert response.status_code == 400

        ip_keys = [key for key in shared_security_cache.rate_keys if ":ip:" in key]
        assert len(set(ip_keys)) == 1
        assert all("not-an-ip" not in key and "203.0.113.77" not in key for key in ip_keys)

    @pytest.mark.anyio
    async def test_registration_fails_closed_when_shared_rate_limit_is_unavailable(
        self, client: AsyncClient, shared_security_cache
    ):
        shared_security_cache.fail_rate_limits = True

        with patch("app.api.v1.invite_codes.register_tenant_with_invite") as register:
            response = await client.post(
                "/api/v1/invite-codes/register",
                headers=_registration_headers(),
                json=self._invalid_registration_payload("INVALID-CACHE-DOWN"),
            )
        register.assert_not_awaited()

        assert response.status_code == 503
        assert response.json()["detail"] == "注册服务暂时不可用，请稍后重试"

    @pytest.mark.anyio
    async def test_create_invite_code_requires_platform_admin(self, client: AsyncClient):
        regular_token = create_access_token("tenant-1", "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Cookie": f"platform_access_token={regular_token}"}
        resp = await client.post(
            "/api/v1/invite-codes",
            json={"tenant_type": "brand"},
            headers=headers,
        )
        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_list_invite_codes_requires_platform_admin(self, client: AsyncClient):
        regular_token = create_access_token("tenant-1", "00000000-0000-0000-0000-000000000001", "admin")
        headers = {"Cookie": f"platform_access_token={regular_token}"}
        resp = await client.get("/api/v1/invite-codes", headers=headers)
        assert resp.status_code == 403
