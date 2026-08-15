"""Enterprise WeChat integration and claim gate tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.campaign import Benefit
from app.models.intent_event import IntentEvent
from app.models.tenant import Tenant
from app.models.wecom import WeComContactWay
from app.services.scan_token import create_scan_token
from app.utils.client_ip import compute_ip_hash
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {
        "Cookie": f"platform_access_token={token}; platform_csrf_token=test-platform-csrf",
        "Origin": "http://localhost:3002",
        "X-Platform-CSRF": "test-platform-csrf",
    }


RULES_JSON = {
    "participation_conditions": "扫码即可参与",
    "claim_limits": "每人限领1次",
    "validity_period": "领取后7天内有效",
    "disclaimer": "最终解释权归品牌方所有",
    "minor_notice": "未成年人请在监护人陪同下参与",
    "customer_service_contact": "400-123-4567",
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
            "name": "企微测试",
            "admin_email": "wecom@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tenant_id = resp.json()["id"]
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    return tenant_id, {"Authorization": f"Bearer {token}"}


def _scan_token(tenant_id: str, public_id: str) -> str:
    return create_scan_token(public_id, compute_ip_hash("127.0.0.1"), tenant_id=tenant_id)


@pytest.mark.anyio
async def test_expired_plan_blocks_consumer_benefit_claim(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_setup,
):
    tenant_id, _ = auth_setup
    tenant_uuid = uuid.UUID(tenant_id)
    benefit = Benefit(
        tenant_id=tenant_uuid,
        name="到期套餐权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=1,
        status="active",
    )
    db_session.add(benefit)
    tenant = await db_session.get(Tenant, tenant_uuid)
    tenant.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    response = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(benefit.id), "scan_token": _scan_token(tenant_id, "EXPIREDPLAN1")},
    )

    assert response.status_code == 403
    assert response.json() == {
        "code": "TENANT_PLAN_EXPIRED",
        "detail": "租户套餐已过期，当前仅支持查看；请联系平台续期",
    }


@pytest.mark.anyio
async def test_expired_plan_blocks_contact_way_before_any_business_write(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_setup,
):
    tenant_id, _ = auth_setup
    tenant_uuid = uuid.UUID(tenant_id)
    benefit = Benefit(
        tenant_id=tenant_uuid,
        name="到期套餐企微权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=1,
        status="active",
    )
    db_session.add(benefit)
    tenant = await db_session.get(Tenant, tenant_uuid)
    tenant.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    response = await client.post(
        "/api/v1/integrations/wecom/contact-way",
        json={"benefit_id": str(benefit.id), "scan_token": _scan_token(tenant_id, "EXPIRED-WECOM")},
    )

    assert response.status_code == 403
    assert response.json() == {
        "code": "TENANT_PLAN_EXPIRED",
        "detail": "租户套餐已过期，当前仅支持查看；请联系平台续期",
    }
    assert await db_session.scalar(select(func.count()).select_from(WeComContactWay)) == 0
    assert await db_session.scalar(select(func.count()).select_from(IntentEvent)) == 0


async def create_product(client: AsyncClient, headers: dict[str, str]) -> str:
    brand = await client.post("/api/v1/brands", json={"name": "企微品牌"}, headers=headers)
    product = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "企微活动产品", "category": "大米"},
        headers=headers,
    )
    return product.json()["id"]


async def create_live_scan_token(
    client: AsyncClient,
    headers: dict[str, str],
    tenant_id: str,
    product_id: str,
    label: str,
) -> str:
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": product_id, "code": f"WECOM-{label}", "name": f"企微规格-{label}"},
        headers=headers,
    )
    assert sku.status_code == 201
    today = datetime.now(UTC).date()
    production_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": product_id,
            "sku_id": sku.json()["id"],
            "batch_code": f"WECOM-PB-{label}",
            "production_date": str(today),
            "expiry_date": str(today + timedelta(days=365)),
        },
        headers=headers,
    )
    assert production_batch.status_code == 201
    code_batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": product_id,
            "sku_id": sku.json()["id"],
            "production_batch_id": production_batch.json()["id"],
            "batch_code": f"WECOM-CB-{label}",
            "quantity": 1,
        },
        headers={
            **headers,
            "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"wecom-claim:{tenant_id}:{label}")),
        },
    )
    assert code_batch.status_code == 201
    batch_id = code_batch.json()["id"]
    exported = await client.post(
        f"/api/v1/code-batches/{batch_id}/export",
        json={"reason": "Test lifecycle setup"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    printing = await client.post(f"/api/v1/code-batches/{batch_id}/mark-printing", headers=headers)
    delivered = await client.post(
        f"/api/v1/code-batches/{batch_id}/mark-delivered",
        json={"reason": "wecom claim fixture", "recipient": "wecom tests", "confirm": "deliver"},
        headers=headers,
    )
    activated = await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
    assert [exported.status_code, printing.status_code, delivered.status_code, activated.status_code] == [200] * 4
    items = await client.get(
        "/api/v1/code-items",
        params={"code_batch_id": batch_id},
        headers=headers,
    )
    assert items.status_code == 200
    resolved = await client.get(
        f"/c/{items.json()['items'][0]['public_id']}",
        headers={"Accept": "application/json"},
    )
    assert resolved.status_code == 200
    return resolved.json()["scan_token"]


@pytest.mark.anyio
async def test_wecom_required_claim_rejects_public_and_mock_confirmation(client: AsyncClient, auth_setup, monkeypatch):
    tenant_id, headers = auth_setup
    product_id = await create_product(client, headers)

    config = await client.post(
        "/api/v1/integrations/wecom",
        json={
            "corp_id": "ww-test",
            "secret": "test-secret",
            "customer_service_user_ids": ["member-a"],
            "mock_mode": True,
        },
        headers=headers,
    )
    assert config.status_code == 200
    verify = await client.post("/api/v1/integrations/wecom/verify", headers=headers)
    assert verify.status_code == 200
    assert verify.json()["connected"] is True

    connector_id = config.json()["connector_id"]
    forged_callback = await client.post(
        f"/api/v1/integrations/wecom/callback/{connector_id}",
        json={"ChangeType": "add_external_contact", "ExternalUserID": "forged"},
    )
    assert forged_callback.status_code == 400

    now = datetime.now(UTC)
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "加企微后领取",
            "campaign_type": "coupon",
            "product_id": product_id,
            "start_at": (now - timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=30)).isoformat(),
            "rules_json": {**RULES_JSON, "wecom_mode": "required"},
        },
        headers=headers,
    )
    assert campaign.status_code == 201
    benefit = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/benefits",
        json={
            "name": "企微领取券",
            "benefit_type": "platform_coupon",
            "config_json": {"validity_type": "campaign_period"},
            "stock_total": 10,
        },
        headers=headers,
    )
    benefit_id = benefit.json()["id"]
    activated_campaign = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/status",
        json={"status": "active"},
        headers=headers,
    )
    assert activated_campaign.status_code == 200
    scan_token = await create_live_scan_token(client, headers, tenant_id, product_id, "REQUIRED")

    blocked = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": benefit_id, "scan_token": scan_token},
    )
    assert blocked.status_code == 403
    detail = blocked.json()["detail"]
    assert detail["code"] == "require_wecom_contact"
    assert detail["qr_code"]

    public_get = await client.get(f"/api/v1/integrations/wecom/mock-added?state={detail['state']}")
    assert public_get.status_code in {401, 405}

    added = await client.post(
        f"/api/v1/integrations/wecom/mock-added?state={detail['state']}",
        headers=headers,
    )
    assert added.status_code == 200

    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    production_mock = await client.post(
        f"/api/v1/integrations/wecom/mock-added?state={detail['state']}",
        headers=headers,
    )
    assert production_mock.status_code == 404
    production_config = await client.post(
        "/api/v1/integrations/wecom",
        json={"corp_id": "ww-production-mock", "mock_mode": True},
        headers=headers,
    )
    assert production_config.status_code == 400
    monkeypatch.setattr(settings, "environment", "test")

    claimed = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": benefit_id, "scan_token": scan_token},
    )
    assert claimed.status_code == 403
    assert claimed.json()["detail"]["code"] == "require_wecom_contact"


@pytest.mark.anyio
async def test_wecom_guide_mode_does_not_block_claim(client: AsyncClient, auth_setup):
    tenant_id, headers = auth_setup
    product_id = await create_product(client, headers)
    now = datetime.now(UTC)
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "引导添加企微",
            "campaign_type": "coupon",
            "product_id": product_id,
            "start_at": (now - timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=30)).isoformat(),
            "rules_json": {**RULES_JSON, "wecom_mode": "guide"},
        },
        headers=headers,
    )
    benefit = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/benefits",
        json={
            "name": "直接领取券",
            "benefit_type": "platform_coupon",
            "config_json": {"validity_type": "campaign_period"},
            "stock_total": 10,
        },
        headers=headers,
    )
    activated_campaign = await client.post(
        f"/api/v1/campaigns/{campaign.json()['id']}/status",
        json={"status": "active"},
        headers=headers,
    )
    assert activated_campaign.status_code == 200
    scan_token = await create_live_scan_token(client, headers, tenant_id, product_id, "GUIDE")
    claimed = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": benefit.json()["id"], "scan_token": scan_token},
    )
    assert claimed.status_code == 201
    assert claimed.json()["status"] == "claimed"


@pytest.mark.anyio
async def test_wecom_management_requires_explicit_permission(client: AsyncClient, auth_setup):
    tenant_id, _ = auth_setup
    viewer_token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    status = await client.get("/api/v1/integrations/wecom", headers=headers)
    saved = await client.post(
        "/api/v1/integrations/wecom",
        json={"corp_id": "ww-denied", "mock_mode": True},
        headers=headers,
    )

    assert status.status_code == 403
    assert status.json()["detail"] == "Missing permission: campaign:manage"
    assert saved.status_code == 403
    assert saved.json()["detail"] == "Missing permission: campaign:manage"


@pytest.mark.anyio
async def test_contact_way_rejects_benefit_from_another_scan_token_tenant(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_setup,
):
    tenant_a, _ = auth_setup
    tenant_b = uuid.uuid4()
    benefit = Benefit(
        tenant_id=tenant_b,
        name="其他租户企微权益",
        benefit_type="platform_coupon",
        config_json={"validity_type": "campaign_period"},
        stock_total=1,
    )
    db_session.add(benefit)
    await db_session.flush()

    response = await client.post(
        "/api/v1/integrations/wecom/contact-way",
        json={
            "benefit_id": str(benefit.id),
            "scan_token": _scan_token(tenant_a, "CROSS-TENANT-WECOM"),
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Benefit not found"


@pytest.mark.anyio
async def test_wecom_callback_cache_outage_is_before_db_bootstrap_and_decrypt(
    client: AsyncClient,
    monkeypatch,
    shared_security_cache,
):
    from app.api.v1 import wecom_integrations
    from app.services import wecom_integration

    original_db_override = app.dependency_overrides[get_db]
    db_dependency_calls = 0
    bootstrap = AsyncMock()
    decrypt = MagicMock()

    async def tracked_db_override():
        nonlocal db_dependency_calls
        db_dependency_calls += 1
        async for session in original_db_override():
            yield session

    app.dependency_overrides[get_db] = tracked_db_override
    monkeypatch.setattr(wecom_integrations, "_scope_public_connector_tenant", bootstrap)
    monkeypatch.setattr(wecom_integration, "decrypt_secrets", decrypt)
    shared_security_cache.fail_rate_limits = True
    try:
        response = await client.post(
            f"/api/v1/integrations/wecom/callback/{uuid.uuid4()}",
            content=b"<xml />",
        )
    finally:
        app.dependency_overrides[get_db] = original_db_override

    assert response.status_code == 503
    assert response.json()["detail"] == "Callback service is temporarily unavailable"
    assert db_dependency_calls == 0
    bootstrap.assert_not_awaited()
    decrypt.assert_not_called()


@pytest.mark.anyio
async def test_wecom_callback_public_bypass_is_exact_route_shape(client: AsyncClient):
    exact_invalid_uuid = await client.post("/api/v1/integrations/wecom/callback/not-a-uuid", content=b"<xml />")
    extra_segment = await client.post(
        f"/api/v1/integrations/wecom/callback/{uuid.uuid4()}/extra",
        content=b"<xml />",
    )

    assert exact_invalid_uuid.status_code == 422
    assert extra_segment.status_code == 401


@pytest.mark.anyio
async def test_wecom_callback_declared_and_chunked_body_caps_run_before_admission_and_db(
    client: AsyncClient,
    monkeypatch,
):
    from app.api.v1 import wecom_integrations
    from app.middleware.request_body_limit import WECOM_CALLBACK_BODY_LIMIT

    admission = AsyncMock()
    original_db_override = app.dependency_overrides[get_db]
    db_dependency_calls = 0

    async def tracked_db_override():
        nonlocal db_dependency_calls
        db_dependency_calls += 1
        async for session in original_db_override():
            yield session

    async def oversized_chunks():
        yield b"a" * 40_000
        yield b"b" * (WECOM_CALLBACK_BODY_LIMIT - 39_999)

    app.dependency_overrides[get_db] = tracked_db_override
    app.dependency_overrides[wecom_integrations.enforce_wecom_callback_ip_admission] = admission
    try:
        declared = await client.post(
            f"/api/v1/integrations/wecom/callback/{uuid.uuid4()}",
            content=b"x" * (WECOM_CALLBACK_BODY_LIMIT + 1),
        )
        chunked_invalid_uuid = await client.post(
            "/api/v1/integrations/wecom/callback/not-a-uuid",
            content=oversized_chunks(),
        )
    finally:
        app.dependency_overrides.pop(wecom_integrations.enforce_wecom_callback_ip_admission, None)
        app.dependency_overrides[get_db] = original_db_override

    assert declared.status_code == 413
    assert chunked_invalid_uuid.status_code == 413
    assert db_dependency_calls == 0
    admission.assert_not_awaited()


@pytest.mark.anyio
async def test_wecom_callback_ip_limit_precedes_bootstrap_and_uses_trusted_proxy(
    client: AsyncClient,
    monkeypatch,
    shared_security_cache,
):
    from app.api.v1 import wecom_integrations
    from app.services import connector_callback_admission

    bootstrap = AsyncMock(return_value=None)
    monkeypatch.setattr(wecom_integrations, "_scope_public_connector_tenant", bootstrap)
    monkeypatch.setattr(connector_callback_admission, "WECOM_CALLBACK_IP_RATE_LIMIT", 1)
    path = f"/api/v1/integrations/wecom/callback/{uuid.uuid4()}"

    first = await client.post(path, content=b"<xml />", headers={"X-Forwarded-For": "203.0.113.31"})
    limited = await client.post(path, content=b"<xml />", headers={"X-Forwarded-For": "203.0.113.31"})
    distinct = await client.post(path, content=b"<xml />", headers={"X-Forwarded-For": "203.0.113.32"})

    assert first.status_code == 404
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert distinct.status_code == 404
    assert bootstrap.await_count == 2
    assert all("203.0.113" not in key for key in shared_security_cache.rate_keys)


@pytest.mark.anyio
async def test_wecom_callback_ignores_spoofed_forwarding_header_from_untrusted_peer(
    client: AsyncClient,
    monkeypatch,
):
    from app.api.v1 import wecom_integrations
    from app.core.config import settings
    from app.services import connector_callback_admission

    bootstrap = AsyncMock(return_value=None)
    monkeypatch.setattr(wecom_integrations, "_scope_public_connector_tenant", bootstrap)
    monkeypatch.setattr(connector_callback_admission, "WECOM_CALLBACK_IP_RATE_LIMIT", 1)
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "")
    path = f"/api/v1/integrations/wecom/callback/{uuid.uuid4()}"

    first = await client.post(path, content=b"<xml />", headers={"X-Forwarded-For": "203.0.113.41"})
    spoofed = await client.post(path, content=b"<xml />", headers={"X-Forwarded-For": "203.0.113.42"})

    assert first.status_code == 404
    assert spoofed.status_code == 429
    assert bootstrap.await_count == 1


@pytest.mark.anyio
async def test_wecom_callback_connector_limit_runs_after_signature_before_event_mutation(
    client: AsyncClient,
    auth_setup,
    monkeypatch,
):
    from app.api.v1 import wecom_integrations
    from app.models.connector import Connector
    from app.services import connector_callback_admission

    tenant_id, _ = auth_setup
    connector = Connector(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        name="signed callback",
        connector_type="wecom_customer_contact",
        config={},
        enabled=True,
    )
    parsed_event = {"Event": "change_external_contact", "ChangeType": "add_external_contact"}
    parse = MagicMock(return_value=parsed_event)
    authority = AsyncMock()
    monkeypatch.setattr(wecom_integrations, "_scope_public_connector_tenant", AsyncMock(return_value=connector))
    monkeypatch.setattr(wecom_integrations, "parse_wecom_callback_body", parse)
    monkeypatch.setattr(wecom_integrations, "apply_verified_wecom_contact_event", authority)
    monkeypatch.setattr(connector_callback_admission, "WECOM_CALLBACK_IDENTITY_RATE_LIMIT", 0)

    response = await client.post(
        f"/api/v1/integrations/wecom/callback/{connector.id}",
        content=b"<xml />",
    )

    assert response.status_code == 429
    parse.assert_called_once()
    authority.assert_not_awaited()


@pytest.mark.anyio
async def test_verified_wecom_callback_retry_preserves_success_and_authority_replay(
    client: AsyncClient,
    auth_setup,
    monkeypatch,
    shared_security_cache,
):
    from app.api.v1 import wecom_integrations
    from app.models.connector import Connector

    tenant_id, _ = auth_setup
    connector = Connector(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        name="verified callback",
        connector_type="wecom_customer_contact",
        config={},
        enabled=True,
    )
    event = {
        "Event": "change_external_contact",
        "ChangeType": "add_external_contact",
        "ExternalUserID": "external-replay",
        "CreateTime": "1700000000",
    }
    authority = AsyncMock(
        side_effect=[
            {"status": "recorded", "replayed": False},
            {"status": "duplicate", "replayed": True},
        ]
    )
    monkeypatch.setattr(wecom_integrations, "_scope_public_connector_tenant", AsyncMock(return_value=connector))
    monkeypatch.setattr(wecom_integrations, "parse_wecom_callback_body", MagicMock(return_value=event))
    monkeypatch.setattr(wecom_integrations, "apply_verified_wecom_contact_event", authority)
    path = f"/api/v1/integrations/wecom/callback/{connector.id}"

    first = await client.post(path, content=b"<xml />")
    replay = await client.post(path, content=b"<xml />")

    assert first.status_code == 200
    assert first.text == "success"
    assert replay.status_code == 200
    assert replay.text == "success"
    assert authority.await_count == 2
    assert sum(key.startswith("wecom-connector:") for key in shared_security_cache.rate_keys) == 2
    assert tenant_id not in "".join(shared_security_cache.rate_keys)
    assert str(connector.id) not in "".join(shared_security_cache.rate_keys)
