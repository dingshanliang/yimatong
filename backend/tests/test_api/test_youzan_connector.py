"""有赞连接器 API 集成测试：类型注册、凭证边界、回调语义与连接测试接线。"""

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.utils.youzan as youzan_protocol
from app.core.database import get_db
from app.main import app
from app.models.connector import BenefitDelivery, Connector
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
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
        yield db_session

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
            "name": "有赞连接器测试租户",
            "admin_email": "youzan@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


def _push_body(client_secret: str, event_type: str, data: dict) -> bytes:
    msg = json.dumps({"type": event_type, "data": data}, separators=(",", ":"))
    sign = hashlib.md5(f"{client_secret}{msg}{client_secret}".encode()).hexdigest().upper()
    return f"msg={quote(msg)}&sign={sign}".encode()


@pytest.mark.anyio
async def test_connector_types_registry_includes_youzan(client: AsyncClient, setup_tenant):
    _tid, headers = setup_tenant
    resp = await client.get("/api/v1/connectors/connectors/types", headers=headers)
    assert resp.status_code == 200
    assert "youzan" in resp.json()["types"]


@pytest.mark.anyio
async def test_create_rejects_client_secret_in_public_config(client: AsyncClient, setup_tenant):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-明文密钥",
            "connector_type": "youzan",
            "config": {"client_id": "cid", "client_secret": "should-be-rejected"},
        },
        headers=headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "client_secret" in str(body)


@pytest.mark.anyio
async def test_create_youzan_connector_stores_secrets_encrypted_and_masks_response(
    client: AsyncClient, setup_tenant, db_session: AsyncSession
):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-测试",
            "connector_type": "youzan",
            "config": {"client_id": "cid-1", "shop_alias": "shop-a"},
            "secrets": {"client_secret": "yz-secret-123456"},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["config"] == {"client_id": "cid-1", "shop_alias": "shop-a"}
    assert "yz-secret-123456" not in resp.text
    row = (await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(data["id"])))).scalar_one()
    assert row.secrets_encrypted
    assert b"yz-secret-123456" not in row.secrets_encrypted


@pytest.mark.anyio
async def test_callback_rejects_bad_signature(client: AsyncClient, setup_tenant):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-回调",
            "connector_type": "youzan",
            "config": {"client_id": "cid"},
            "secrets": {"client_secret": "sec-callback"},
        },
        headers=headers,
    )
    connector_id = resp.json()["id"]

    bad = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("wrong-secret", "coupon_take_succeeded", {"record_id": "R1", "status": "success"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert bad.status_code == 403


@pytest.mark.anyio
async def test_callback_ignores_unrelated_events_without_422(client: AsyncClient, setup_tenant, shared_security_cache):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-忽略事件",
            "connector_type": "youzan",
            "config": {"client_id": "cid"},
            "secrets": {"client_secret": "sec-ignore"},
        },
        headers=headers,
    )
    connector_id = resp.json()["id"]

    unknown = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("sec-ignore", "trade_paid", {"tid": "T1"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert unknown.status_code == 200
    assert unknown.json() == {"status": "ignored"}

    consume_no_match = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("sec-ignore", "coupon_consume_succeeded", {"coupon_no": "NO-SUCH"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert consume_no_match.status_code == 200
    assert consume_no_match.json() == {"status": "ignored", "coupon_transition": "no_match"}


@pytest.mark.anyio
async def test_callback_grant_event_settles_matching_delivery(
    client: AsyncClient, setup_tenant, db_session: AsyncSession, shared_security_cache
):
    tenant_id, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-结算",
            "connector_type": "youzan",
            "config": {"client_id": "cid"},
            "secrets": {"client_secret": "sec-settle"},
        },
        headers=headers,
    )
    connector_id = uuid.UUID(resp.json()["id"])
    claim_id = uuid.uuid4()
    delivery = BenefitDelivery(
        tenant_id=uuid.UUID(tenant_id),
        connector_id=connector_id,
        claim_id=claim_id,
        consumer_id="consumer-youzan-001",
        benefit_type="platform_coupon",
        benefit_config={},
        external_id="YZ-GRANT-SETTLE-1",
        status="pending",
    )
    db_session.add(delivery)
    await db_session.flush()

    settle = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body(
            "sec-settle", "coupon_take_succeeded", {"record_id": "YZ-GRANT-SETTLE-1", "status": "success"}
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert settle.status_code == 200, settle.text
    assert settle.json() == {"status": "ok"}
    await db_session.refresh(delivery)
    assert delivery.status == "success"
    assert delivery.external_data["_callback_external_id"] == "YZ-GRANT-SETTLE-1"


@pytest.mark.anyio
async def test_connection_endpoint_wires_adapter_test_connection(client: AsyncClient, setup_tenant, monkeypatch):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-测试连接",
            "connector_type": "youzan",
            "config": {"client_id": "cid"},
            "secrets": {"client_secret": "sec-test"},
        },
        headers=headers,
    )
    connector_id = resp.json()["id"]

    async def ok_token(**kwargs):
        return {"access_token": "at"}

    monkeypatch.setattr(youzan_protocol, "fetch_token", ok_token)
    ok = await client.post(f"/api/v1/connectors/connectors/{connector_id}/test", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["success"] is True

    async def boom(**kwargs):
        raise youzan_protocol.YouzanAPIError(status_code=401, code=None, message="bad credential")

    monkeypatch.setattr(youzan_protocol, "fetch_token", boom)
    failed = await client.post(f"/api/v1/connectors/connectors/{connector_id}/test", headers=headers)
    assert failed.status_code == 200
    assert failed.json()["success"] is False


@pytest.mark.anyio
async def test_callback_consume_event_routes_wallet_transition(
    client: AsyncClient, setup_tenant, monkeypatch, shared_security_cache
):
    """核销事件经回调端点转交钱包服务并返回 consumed；接线参数带租户与连接器过滤。"""
    from app.services import external_coupon_wallet

    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "有赞-核销回流",
            "connector_type": "youzan",
            "config": {"client_id": "cid"},
            "secrets": {"client_secret": "sec-consume"},
        },
        headers=headers,
    )
    connector_id = resp.json()["id"]

    calls = []

    async def fake_consume(db, tenant_id, connector_id_, ref, external_data):
        calls.append((tenant_id, connector_id_, ref, external_data))
        return True

    monkeypatch.setattr(external_coupon_wallet, "consume_external_coupon", fake_consume)

    consumed = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("sec-consume", "coupon_consume_succeeded", {"coupon_no": "YZ-CODE-1"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert consumed.status_code == 200
    assert consumed.json() == {"status": "ignored", "coupon_transition": "consumed"}
    assert len(calls) == 1
    assert calls[0][1] == uuid.UUID(connector_id)
    assert calls[0][2] == "YZ-CODE-1"
    assert calls[0][3]["event"] == "coupon_consume_succeeded"
