"""W19: Webhook / Open API 测试"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import webhooks as webhook_api
from app.api.v1.webhooks import ApiKeyCreate
from app.core.database import get_db
from app.main import app
from app.models.webhook import ApiKey
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _api_key_body(name: str, role: str = "data_reader") -> dict:
    return {
        "name": name,
        "role": role,
        "expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
    }


def _lifecycle_headers(headers: dict, sequence: int = 1) -> dict:
    return {
        **headers,
        "Idempotency-Key": f"00000000-0000-4000-8000-{sequence:012d}",
    }


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
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def setup_tenant(client: AsyncClient):
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "Webhook测试租户",
            "admin_email": "webhook@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(
        tid,
        "00000000-0000-0000-0000-000000000001",
        "admin",
        extra={"sid": "00000000-0000-0000-0000-000000000002"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


class TestWebhookManagement:
    """W19-001: Webhook 端点管理"""

    @pytest.mark.anyio
    async def test_create_webhook(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/webhooks/endpoints",
            json={
                "url": "https://example.com/webhook",
                "events": ["scan.created", "claim.created"],
                "description": "测试端点",
                "batch_mode": False,
                "batch_size": 100,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/webhook"
        assert "scan.created" in data["events"]
        assert "secret" in data
        assert data["secret"].startswith("whsec_")
        assert data["batch_mode"] is False
        assert data["batch_size"] == 100

    @pytest.mark.anyio
    async def test_list_webhooks(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/webhooks/endpoints",
            json={
                "url": "https://example.com/wh2",
                "events": ["risk.alert"],
            },
            headers=headers,
        )

        resp = await client.get("/api/v1/webhooks/endpoints", headers=headers)
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1

    @pytest.mark.anyio
    async def test_update_webhook(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/webhooks/endpoints",
            json={
                "url": "https://example.com/wh3",
                "events": ["scan.created"],
            },
            headers=headers,
        )
        endpoint_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/webhooks/endpoints/{endpoint_id}",
            json={"enabled": False, "description": "已禁用"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["description"] == "已禁用"

    @pytest.mark.anyio
    async def test_delete_webhook(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/webhooks/endpoints",
            json={
                "url": "https://example.com/wh4",
                "events": ["scan.created"],
            },
            headers=headers,
        )
        endpoint_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/webhooks/endpoints/{endpoint_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


class TestApiKey:
    """W19-002: API Key 认证"""

    def test_issue_schema_normalizes_and_enforces_expiry_contract(self):
        future = datetime.now(UTC) + timedelta(days=90)

        expiring = ApiKeyCreate.model_validate(
            {
                "name": "  ERP 同步  ",
                "role": "data_reader",
                "expires_at": future.isoformat(),
            }
        )
        assert expiring.name == "ERP 同步"
        assert expiring.expires_at == future

        permanent = ApiKeyCreate.model_validate(
            {
                "name": "受控旧系统",
                "role": "erp_sync",
                "expires_at": None,
                "permanent_acknowledged": True,
                "permanent_reason": "设备固件暂不支持自动轮换凭证",
            }
        )
        assert permanent.permanent_reason == "设备固件暂不支持自动轮换凭证"

        invalid_payloads = [
            {"name": "   ", "role": "data_reader", "expires_at": future.isoformat()},
            {"name": "ERP", "role": "admin", "expires_at": future.isoformat()},
            {"name": "ERP", "role": "data_reader", "expires_at": datetime.now().isoformat()},
            {
                "name": "ERP",
                "role": "data_reader",
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
            {
                "name": "ERP",
                "role": "data_reader",
                "expires_at": future.isoformat(),
                "permanent_acknowledged": True,
                "permanent_reason": "仅永久密钥才允许提交风险确认原因",
            },
            {"name": "ERP", "role": "data_reader", "expires_at": None},
            {
                "name": "ERP",
                "role": "data_reader",
                "expires_at": None,
                "permanent_acknowledged": True,
                "permanent_reason": "太短",
            },
        ]
        for payload in invalid_payloads:
            with pytest.raises(ValidationError):
                ApiKeyCreate.model_validate(payload)

    def test_issue_schema_limits_finite_credentials_to_exactly_365_days(self, monkeypatch):
        fixed_now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

        monkeypatch.setattr(webhook_api, "datetime", FrozenDateTime)
        exact_boundary = fixed_now + timedelta(days=365)

        accepted = ApiKeyCreate.model_validate(
            {
                "name": "365 天轮换密钥",
                "role": "data_reader",
                "expires_at": exact_boundary.isoformat(),
            }
        )
        assert accepted.expires_at == exact_boundary

        for expires_at in (exact_boundary + timedelta(microseconds=1), datetime.max.replace(tzinfo=UTC)):
            with pytest.raises(ValidationError, match="365"):
                ApiKeyCreate.model_validate(
                    {
                        "name": "超长期密钥",
                        "role": "data_reader",
                        "expires_at": expires_at.isoformat(),
                    }
                )

    @pytest.mark.anyio
    async def test_issue_and_rotate_require_canonical_idempotency_keys(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant
        body = {
            "name": "ERP 同步",
            "role": "data_reader",
            "expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
        }

        missing = await client.post("/api/v1/webhooks/api-keys", json=body, headers=headers)
        malformed = await client.post(
            "/api/v1/webhooks/api-keys",
            json=body,
            headers={**headers, "Idempotency-Key": "not-a-canonical-uuid"},
        )
        rotate_missing = await client.post(
            "/api/v1/webhooks/api-keys/00000000-0000-0000-0000-000000000099/rotate",
            headers=headers,
        )

        assert missing.status_code == 422
        assert malformed.status_code == 422
        assert rotate_missing.status_code == 422

    @pytest.mark.anyio
    async def test_issue_rejects_finite_expiry_beyond_365_days_at_the_http_boundary(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers = setup_tenant
        request_headers = _lifecycle_headers(headers)
        too_far = datetime.now(UTC) + timedelta(days=365, minutes=1)

        responses = [
            await client.post(
                "/api/v1/webhooks/api-keys",
                json={"name": "超长期密钥", "role": "data_reader", "expires_at": expires_at},
                headers=request_headers,
            )
            for expires_at in (too_far.isoformat(), datetime.max.replace(tzinfo=UTC).isoformat())
        ]

        assert {response.status_code for response in responses} == {422}

    @pytest.mark.anyio
    async def test_lifecycle_rate_limit_fails_closed_when_shared_cache_is_unavailable(
        self,
        client: AsyncClient,
        setup_tenant,
        shared_security_cache,
    ):
        _, headers = setup_tenant
        shared_security_cache.fail_rate_limits = True

        response = await client.post(
            "/api/v1/webhooks/api-keys",
            json={
                "name": "ERP 同步",
                "role": "data_reader",
                "expires_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
            },
            headers={**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
        )

        assert response.status_code == 503

    @pytest.mark.anyio
    async def test_lifecycle_rate_limit_uses_hmac_buckets_and_returns_retry_after(
        self,
        client: AsyncClient,
        setup_tenant,
        shared_security_cache,
        monkeypatch,
    ):
        tenant_id, headers = setup_tenant
        actor_id = "00000000-0000-0000-0000-000000000001"
        future = datetime.now(UTC) + timedelta(days=90)

        async def issued(*_args, **_kwargs):
            return SimpleNamespace(
                id="00000000-0000-4000-8000-000000000101",
                name="ERP 同步",
                key="ymt_" + "a" * 48,
                key_prefix="ymt_aaaaaaaa",
                role="data_reader",
                permissions=["scan:list"],
                expires_at=future,
            )

        monkeypatch.setattr(webhook_api, "create_api_key", issued)
        request_headers = {**headers, "Idempotency-Key": "11111111-1111-4111-8111-111111111111"}
        body = {"name": "ERP 同步", "role": "data_reader", "expires_at": future.isoformat()}

        first = await client.post("/api/v1/webhooks/api-keys", json=body, headers=request_headers)
        assert first.status_code == 201
        assert len(shared_security_cache.rate_keys) == 2
        assert all(tenant_id not in key and actor_id not in key for key in shared_security_cache.rate_keys)
        principal_key = next(key for key in shared_security_cache.rate_keys if key.startswith("principal:"))
        shared_security_cache.rate_counts[principal_key] = 20

        blocked = await client.post("/api/v1/webhooks/api-keys", json=body, headers=request_headers)

        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"

    @pytest.mark.anyio
    async def test_create_api_key_with_role(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("外部系统密钥"),
            headers=_lifecycle_headers(headers),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "外部系统密钥"
        assert data["key"].startswith("ymt_")
        assert data["key_prefix"] == data["key"][:12]
        assert data["role"] == "data_reader"
        assert "scan:list" in data["permissions"]

    @pytest.mark.anyio
    async def test_active_cap_does_not_count_expired_credentials(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        tenant_id, headers = setup_tenant
        db_session.add_all(
            [
                ApiKey(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(tenant_id),
                    name=f"expired-{index}",
                    key_prefix=f"expired{index:05d}",
                    key_digest=f"{index:064x}",
                    role="data_reader",
                    permissions=["scan:list"],
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
                for index in range(20)
            ]
        )
        await db_session.flush()

        response = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("未过期容量"),
            headers=_lifecycle_headers(headers),
        )

        assert response.status_code == 201

    @pytest.mark.anyio
    async def test_issue_replays_same_secret_and_rejects_changed_payload(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers = setup_tenant
        request_headers = _lifecycle_headers(headers)
        body = _api_key_body("幂等签发")

        first = await client.post("/api/v1/webhooks/api-keys", json=body, headers=request_headers)
        replay = await client.post("/api/v1/webhooks/api-keys", json=body, headers=request_headers)
        conflict = await client.post(
            "/api/v1/webhooks/api-keys",
            json={**body, "name": "改变后的签发"},
            headers=request_headers,
        )

        assert first.status_code == replay.status_code == 201
        assert first.json()["id"] == replay.json()["id"]
        assert first.json()["key"] == replay.json()["key"]
        assert conflict.status_code == 409

    @pytest.mark.anyio
    async def test_permanent_issue_persists_and_audits_the_normalized_reason(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        setup_tenant,
    ):
        _, headers = setup_tenant
        reason = "设备固件暂不支持自动轮换，已登记人工保管和季度复核"

        response = await client.post(
            "/api/v1/webhooks/api-keys",
            json={
                "name": " 受控旧系统 ",
                "role": "erp_sync",
                "expires_at": None,
                "permanent_acknowledged": True,
                "permanent_reason": f" {reason} ",
            },
            headers=_lifecycle_headers(headers),
        )
        assert response.status_code == 201
        stored = await db_session.get(ApiKey, uuid.UUID(response.json()["id"]))
        assert stored is not None
        audit = await client.get("/api/v1/audit-logs?page_size=100", headers=headers)
        issued_audit = next(item for item in audit.json()["items"] if item["resource"] == f"api_key:{stored.id}")

        assert stored.expires_at is None
        assert stored.permanent_reason == reason
        assert issued_audit["details"]["permanent_reason"] == reason
        assert response.json()["key"] not in str(issued_audit)

    @pytest.mark.anyio
    async def test_rotate_replays_after_old_key_is_revoked_and_rejects_a_new_attempt(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("幂等轮换"),
            headers=_lifecycle_headers(headers, 1),
        )
        key_id = created.json()["id"]
        replay_headers = _lifecycle_headers(headers, 2)

        first = await client.post(f"/api/v1/webhooks/api-keys/{key_id}/rotate", headers=replay_headers)
        replay = await client.post(f"/api/v1/webhooks/api-keys/{key_id}/rotate", headers=replay_headers)
        new_attempt = await client.post(
            f"/api/v1/webhooks/api-keys/{key_id}/rotate",
            headers=_lifecycle_headers(headers, 3),
        )

        assert first.status_code == replay.status_code == 201
        assert first.json()["id"] == replay.json()["id"]
        assert first.json()["key"] == replay.json()["key"]
        assert new_attempt.status_code == 409

    @pytest.mark.anyio
    async def test_create_api_key_invalid_role(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("无效角色", "super_admin"),
            headers=_lifecycle_headers(headers),
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("role", "tenant_type", "with_session"),
        [
            ("viewer", "brand", True),
            ("operator", "brand", True),
            ("admin", "brand", False),
            ("admin", "agency", True),
        ],
    )
    async def test_only_durable_brand_admin_can_issue_api_keys(
        self,
        client: AsyncClient,
        setup_tenant,
        role: str,
        tenant_type: str,
        with_session: bool,
    ):
        tid, _ = setup_tenant
        extra = {"sid": "00000000-0000-0000-0000-000000000003"} if with_session else None
        token = create_access_token(
            tid,
            "00000000-0000-0000-0000-000000000004",
            role,
            tenant_type=tenant_type,
            extra=extra,
        )

        resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("越权密钥", "full_access"),
            headers=_lifecycle_headers({"Authorization": f"Bearer {token}"}),
        )

        assert resp.status_code == 403

    @pytest.mark.anyio
    async def test_api_key_role_must_not_accept_web_roles(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant

        resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("错误角色密钥", "admin"),
            headers=_lifecycle_headers(headers),
        )

        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_list_api_keys_returns_a_bounded_paginated_contract(self, client: AsyncClient, setup_tenant):
        _, headers = setup_tenant

        response = await client.get("/api/v1/webhooks/api-keys?page=2&page_size=10", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "page": 2, "page_size": 10}

        too_large = await client.get("/api/v1/webhooks/api-keys?page=1&page_size=101", headers=headers)
        assert too_large.status_code == 422

    @pytest.mark.anyio
    async def test_list_api_keys(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("列表测试密钥", "coupon_operator"),
            headers=_lifecycle_headers(headers),
        )

        resp = await client.get("/api/v1/webhooks/api-keys", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1
        assert items[0]["role"] == "coupon_operator"
        assert items[0]["key_prefix"].startswith("ymt_")
        assert "key" not in items[0]
        assert "key_digest" not in items[0]

    @pytest.mark.anyio
    async def test_rotate_api_key_returns_new_secret_once_and_invalidates_old_key(
        self,
        client: AsyncClient,
        setup_tenant,
        monkeypatch,
    ):
        _, headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("待轮换密钥"),
            headers=_lifecycle_headers(headers, 1),
        )
        assert created.status_code == 201
        old_secret = created.json()["key"]
        key_id = created.json()["id"]

        rotated = await client.post(
            f"/api/v1/webhooks/api-keys/{key_id}/rotate",
            headers=_lifecycle_headers(headers, 2),
        )

        assert rotated.status_code == 201
        new_secret = rotated.json()["key"]
        assert new_secret.startswith("ymt_")
        assert rotated.json()["key_prefix"] == new_secret[:12]
        assert new_secret != old_secret
        assert rotated.json()["id"] != key_id

        listed = await client.get("/api/v1/webhooks/api-keys", headers=headers)
        assert listed.status_code == 200
        assert all("key" not in item and "key_digest" not in item for item in listed.json()["items"])

        import app.core.database as database

        with monkeypatch.context() as context:
            context.setattr(database, "async_session_factory", TestSessionLocal)
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": old_secret})).status_code == 401
            assert (await client.get("/open/v1/scans", headers={"X-Api-Key": new_secret})).status_code == 200

    @pytest.mark.anyio
    async def test_expired_plan_allows_only_api_key_revoke(
        self,
        client: AsyncClient,
        setup_tenant,
        monkeypatch,
    ):
        _, headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("到期后收权"),
            headers=_lifecycle_headers(headers, 1),
        )
        assert created.status_code == 201
        key_id = created.json()["id"]

        async def plan_expired(*_args, **_kwargs):
            return True

        monkeypatch.setattr("app.middleware.tenant.TenantScopeMiddleware._tenant_plan_blocks_write", plan_expired)

        issue = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("禁止签发"),
            headers=_lifecycle_headers(headers, 2),
        )
        rotate = await client.post(
            f"/api/v1/webhooks/api-keys/{key_id}/rotate",
            headers=_lifecycle_headers(headers, 3),
        )
        revoke = await client.delete(f"/api/v1/webhooks/api-keys/{key_id}", headers=headers)

        assert issue.status_code == 403
        assert rotate.status_code == 403
        assert revoke.status_code == 200

    @pytest.mark.anyio
    async def test_api_key_lifecycle_is_actor_bound_and_never_audits_the_secret(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("审计密钥"),
            headers=_lifecycle_headers(headers, 1),
        )
        secret = created.json()["key"]
        rotated = await client.post(
            f"/api/v1/webhooks/api-keys/{created.json()['id']}/rotate",
            headers=_lifecycle_headers(headers, 2),
        )
        await client.delete(f"/api/v1/webhooks/api-keys/{rotated.json()['id']}", headers=headers)

        audit = await client.get("/api/v1/audit-logs?page_size=100", headers=headers)

        assert audit.status_code == 200
        lifecycle = [item for item in audit.json()["items"] if item["action"].startswith("api_key_")]
        assert {item["action"] for item in lifecycle} == {
            "api_key_issued",
            "api_key_rotated",
            "api_key_revoked",
        }
        assert {item["operator"]["id"] for item in lifecycle} == {"00000000-0000-0000-0000-000000000001"}
        assert secret not in str(lifecycle)
        assert rotated.json()["key"] not in str(lifecycle)

    @pytest.mark.anyio
    async def test_api_key_issue_rolls_back_when_audit_fails(
        self,
        client: AsyncClient,
        setup_tenant,
        monkeypatch,
    ):
        _, headers = setup_tenant

        async def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("app.services.webhook.write_audit_log", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                "/api/v1/webhooks/api-keys",
                json=_api_key_body("必须回滚"),
                headers=_lifecycle_headers(headers),
            )

        listed = await client.get("/api/v1/webhooks/api-keys", headers=headers)
        assert listed.status_code == 200
        assert all(item["name"] != "必须回滚" for item in listed.json()["items"])

    @pytest.mark.anyio
    async def test_viewer_cannot_list_rotate_or_revoke_api_keys(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        tid, admin_headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("受保护密钥"),
            headers=_lifecycle_headers(admin_headers),
        )
        key_id = created.json()["id"]
        viewer_token = create_access_token(
            tid,
            "00000000-0000-0000-0000-000000000010",
            "viewer",
            tenant_type="brand",
            extra={"sid": "00000000-0000-0000-0000-000000000011"},
        )
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

        responses = [
            await client.get("/api/v1/webhooks/api-keys", headers=viewer_headers),
            await client.post(
                f"/api/v1/webhooks/api-keys/{key_id}/rotate",
                headers=_lifecycle_headers(viewer_headers),
            ),
            await client.delete(f"/api/v1/webhooks/api-keys/{key_id}", headers=viewer_headers),
        ]

        assert {response.status_code for response in responses} == {403}

    @pytest.mark.anyio
    async def test_api_key_lifecycle_ids_are_tenant_scoped(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, tenant_a_headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("租户 A 密钥"),
            headers=_lifecycle_headers(tenant_a_headers),
        )
        tenant_a_key_id = created.json()["id"]

        tenant_b = await client.post(
            "/api/v1/tenants",
            json={
                "name": "Webhook隔离租户B",
                "admin_email": "webhook-b@test.com",
                "admin_name": "Admin B",
                "admin_password": "Pass1234",
            },
            headers=_platform_admin_headers(),
        )
        assert tenant_b.status_code == 201
        tenant_b_token = create_access_token(
            tenant_b.json()["id"],
            "00000000-0000-0000-0000-000000000012",
            "admin",
            tenant_type="brand",
            extra={"sid": "00000000-0000-0000-0000-000000000013"},
        )
        tenant_b_headers = {"Authorization": f"Bearer {tenant_b_token}"}

        listed = await client.get("/api/v1/webhooks/api-keys", headers=tenant_b_headers)
        rotate = await client.post(
            f"/api/v1/webhooks/api-keys/{tenant_a_key_id}/rotate",
            headers=_lifecycle_headers(tenant_b_headers),
        )
        revoke = await client.delete(
            f"/api/v1/webhooks/api-keys/{tenant_a_key_id}",
            headers=tenant_b_headers,
        )

        assert listed.status_code == 200
        assert all(item["id"] != tenant_a_key_id for item in listed.json()["items"])
        assert rotate.status_code == 404
        assert revoke.status_code == 404

    @pytest.mark.anyio
    async def test_api_key_cannot_authenticate_admin_lifecycle_routes(
        self,
        client: AsyncClient,
        setup_tenant,
    ):
        _, headers = setup_tenant
        created = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("不能反向管理", "full_access"),
            headers=_lifecycle_headers(headers),
        )

        response = await client.get(
            "/api/v1/webhooks/api-keys",
            headers={"X-Api-Key": created.json()["key"]},
        )

        assert response.status_code == 401

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("sqlstate", "expected_status"),
        [
            ("22023", 400),
            ("28000", 401),
            ("42501", 403),
            ("40001", 409),
            ("54000", 409),
            ("23505", 409),
        ],
    )
    async def test_api_key_database_failures_have_stable_http_statuses(
        self,
        client: AsyncClient,
        setup_tenant,
        monkeypatch,
        sqlstate: str,
        expected_status: int,
    ):
        _, headers = setup_tenant

        class PgFailure(Exception):
            pass

        original = PgFailure("database rejected lifecycle transition")
        original.sqlstate = sqlstate

        async def fail_issue(*_args, **_kwargs):
            raise DBAPIError("SELECT issue_api_key(...) ", {}, original, False)

        monkeypatch.setattr("app.api.v1.webhooks.create_api_key", fail_issue)
        response = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("数据库拒绝"),
            headers=_lifecycle_headers(headers),
        )

        assert response.status_code == expected_status

    @pytest.mark.anyio
    async def test_revoke_api_key(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        create_resp = await client.post(
            "/api/v1/webhooks/api-keys",
            json=_api_key_body("待吊销密钥"),
            headers=_lifecycle_headers(headers),
        )
        key_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/webhooks/api-keys/{key_id}",
            headers=headers,
        )
        assert resp.status_code == 200


class TestEventDelivery:
    """W19-003: 事件推送"""

    @pytest.mark.anyio
    async def test_list_deliveries(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.get(
            "/api/v1/webhooks/deliveries",
            headers=headers,
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_list_deliveries_with_status_filter(self, client: AsyncClient, setup_tenant):
        tid, headers = setup_tenant
        resp = await client.get(
            "/api/v1/webhooks/deliveries?status=failed",
            headers=headers,
        )
        assert resp.status_code == 200
