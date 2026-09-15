"""微盟连接器 API 集成测试：类型注册、凭证边界、回调 ACK 语义与连接测试接线。"""

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.utils.weimob as weimob_protocol
from app.core.database import get_db
from app.main import app
from app.models.connector import BenefitDelivery, Connector
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal

WEIMOB_ACK = {"code": {"errcode": 0, "errmsg": "success"}}


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
            "name": "微盟连接器测试租户",
            "admin_email": "weimob@test.com",
            "admin_name": "Admin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}
    return tid, headers


def _push_body(client_id: str, client_secret: str, event: str, msg_body: dict, *, msg_id: str = "push-1") -> bytes:
    compact = json.dumps(msg_body, separators=(",", ":"), ensure_ascii=False)
    sign = hashlib.md5(f"{client_id}{msg_id}{compact}{client_secret}".encode()).hexdigest()
    return json.dumps(
        {
            "id": msg_id,
            "topic": "weimob_crm.coupon",
            "event": event,
            "bosId": "bos-1",
            "sign": sign,
            "msgBody": msg_body,
        }
    ).encode()


async def _create_weimob_connector(client: AsyncClient, headers: dict, name: str, secret: str) -> str:
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": name,
            "connector_type": "weimob",
            "config": {
                "client_id": "cid",
                "shop_id": "shop-1",
                "shop_type": "public_account_id",
                "vid": "6000014039354",
                "vid_type": "2",
            },
            "secrets": {"client_secret": secret},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.anyio
async def test_connector_types_registry_includes_weimob(client: AsyncClient, setup_tenant):
    _tid, headers = setup_tenant
    resp = await client.get("/api/v1/connectors/connectors/types", headers=headers)
    assert resp.status_code == 200
    assert "weimob" in resp.json()["types"]


@pytest.mark.anyio
async def test_create_rejects_client_secret_in_public_config(client: AsyncClient, setup_tenant):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "微盟-明文密钥",
            "connector_type": "weimob",
            "config": {"client_id": "cid", "client_secret": "should-be-rejected"},
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert "client_secret" in str(resp.json())


@pytest.mark.anyio
async def test_create_weimob_connector_stores_secrets_encrypted_and_masks_response(
    client: AsyncClient, setup_tenant, db_session: AsyncSession
):
    _tid, headers = setup_tenant
    resp = await client.post(
        "/api/v1/connectors/connectors",
        json={
            "name": "微盟-测试",
            "connector_type": "weimob",
            "config": {
                "client_id": "cid-1",
                "shop_id": "shop-1",
                "shop_type": "public_account_id",
                "vid": "6000014039354",
                "vid_type": "2",
            },
            "secrets": {"client_secret": "wm-secret-123456"},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["config"]["client_id"] == "cid-1"
    assert data["config"]["shop_id"] == "shop-1"
    assert "wm-secret-123456" not in resp.text
    row = (await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(data["id"])))).scalar_one()
    assert row.secrets_encrypted
    assert b"wm-secret-123456" not in row.secrets_encrypted


@pytest.mark.anyio
async def test_callback_rejects_bad_signature(client: AsyncClient, setup_tenant):
    _tid, headers = setup_tenant
    connector_id = await _create_weimob_connector(client, headers, "微盟-回调", "sec-callback")

    bad = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("cid", "wrong-secret", "receiveCoupon", {"code": "WM-1"}),
        headers={"Content-Type": "application/json"},
    )
    assert bad.status_code == 403


@pytest.mark.anyio
async def test_callback_ignores_unrelated_events_with_weimob_ack(
    client: AsyncClient, setup_tenant, shared_security_cache
):
    """无关事件不 422，并按微盟 ACK 契约响应（否则平台重推 5 次）。"""
    _tid, headers = setup_tenant
    connector_id = await _create_weimob_connector(client, headers, "微盟-忽略事件", "sec-ignore")

    unknown = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("cid", "sec-ignore", "orderPaid", {"orderNo": "O1"}),
        headers={"Content-Type": "application/json"},
    )
    assert unknown.status_code == 200
    assert unknown.json() == WEIMOB_ACK

    consume_no_match = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("cid", "sec-ignore", "consumeCoupon", {"code": "NO-SUCH"}),
        headers={"Content-Type": "application/json"},
    )
    assert consume_no_match.status_code == 200
    assert consume_no_match.json() == WEIMOB_ACK


@pytest.mark.anyio
async def test_callback_grant_event_settles_matching_delivery(
    client: AsyncClient, setup_tenant, db_session: AsyncSession, shared_security_cache
):
    tenant_id, headers = setup_tenant
    connector_id = uuid.UUID(await _create_weimob_connector(client, headers, "微盟-结算", "sec-settle"))
    claim_id = uuid.uuid4()
    delivery = BenefitDelivery(
        tenant_id=uuid.UUID(tenant_id),
        connector_id=connector_id,
        claim_id=claim_id,
        consumer_id="consumer-weimob-001",
        benefit_type="platform_coupon",
        benefit_config={},
        external_id="WM-GRANT-SETTLE-1",
        status="pending",
    )
    db_session.add(delivery)
    await db_session.flush()

    settle = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("cid", "sec-settle", "receiveCoupon", {"code": "WM-GRANT-SETTLE-1"}),
        headers={"Content-Type": "application/json"},
    )
    assert settle.status_code == 200, settle.text
    assert settle.json() == WEIMOB_ACK
    await db_session.refresh(delivery)
    assert delivery.status == "success"
    assert delivery.external_data["_callback_external_id"] == "WM-GRANT-SETTLE-1"


@pytest.mark.anyio
async def test_callback_consume_event_routes_wallet_transition(
    client: AsyncClient, setup_tenant, monkeypatch, shared_security_cache
):
    """核销事件经回调端点转交钱包服务并返回 consumed；响应仍按微盟 ACK 契约。"""
    from app.services import external_coupon_wallet

    _tid, headers = setup_tenant
    connector_id = await _create_weimob_connector(client, headers, "微盟-核销回流", "sec-consume")

    calls = []

    async def fake_consume(db, tenant_id, connector_id_, ref, external_data):
        calls.append((tenant_id, connector_id_, ref, external_data))
        return True

    monkeypatch.setattr(external_coupon_wallet, "consume_external_coupon", fake_consume)

    consumed = await client.post(
        f"/api/v1/connectors/connectors/{connector_id}/callback",
        content=_push_body("cid", "sec-consume", "consumeCoupon", {"code": "WM-CODE-1"}),
        headers={"Content-Type": "application/json"},
    )
    assert consumed.status_code == 200
    assert consumed.json() == WEIMOB_ACK
    assert len(calls) == 1
    assert calls[0][1] == uuid.UUID(connector_id)
    assert calls[0][2] == "WM-CODE-1"
    assert calls[0][3]["event"] == "consumeCoupon"


@pytest.mark.anyio
async def test_connection_endpoint_wires_adapter_test_connection(client: AsyncClient, setup_tenant, monkeypatch):
    _tid, headers = setup_tenant
    connector_id = await _create_weimob_connector(client, headers, "微盟-测试连接", "sec-test")

    async def ok_token(**kwargs):
        return {"access_token": "at", "expires_in": 604799}

    monkeypatch.setattr(weimob_protocol, "fetch_token", ok_token)
    ok = await client.post(f"/api/v1/connectors/connectors/{connector_id}/test", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["success"] is True

    async def boom(**kwargs):
        raise weimob_protocol.WeimobAPIError(status_code=401, code=None, message="bad credential")

    monkeypatch.setattr(weimob_protocol, "fetch_token", boom)
    failed = await client.post(f"/api/v1/connectors/connectors/{connector_id}/test", headers=headers)
    assert failed.status_code == 200
    assert failed.json()["success"] is False


@pytest.mark.anyio
async def test_external_coupon_closure_is_adapter_agnostic():
    """S5 契约：claim preflight 与外部券钱包不携带任何适配器特判，微盟零改动复用。"""
    import inspect

    import app.api.v1.benefit_claims as benefit_claims_module
    import app.services.external_coupon_wallet as wallet_module

    preflight_source = inspect.getsource(benefit_claims_module)
    assert "external_coupon_template_missing" in preflight_source
    # preflight 以 connector_id + coupon_id 通用键判定，不含平台特判
    assert "youzan" not in preflight_source.lower()
    assert "weimob" not in preflight_source.lower()

    wallet_source = inspect.getsource(wallet_module)
    assert "issue_external_member_coupon" in wallet_source
    assert "youzan" not in wallet_source.lower()
    assert "weimob" not in wallet_source.lower()
