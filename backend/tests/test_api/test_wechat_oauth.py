import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v1 import wechat_oauth
from app.models.member import ConsumerProfile
from app.services.connectors.wechat_pay_transfer import WeChatPayTransferAdapter
from app.services.scan_token import create_scan_token, verify_scan_token
from app.utils.client_ip import compute_ip_hash
from app.utils.crypto import decrypt_wechat_openid, hash_wechat_openid


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("127.0.0.1", 1)})


def test_auth_start_requires_explicit_consent():
    with pytest.raises(ValidationError):
        wechat_oauth.WeChatOAuthStart(benefit_id=uuid.uuid4(), scan_token="token")


@pytest.mark.anyio
async def test_cash_connector_configuration_requires_oauth_secret():
    valid, message = await WeChatPayTransferAdapter().validate_config(
        {
            "oa_appid": "wx-app",
            "mch_id": "merchant",
            "cert_serial_no": "serial",
            "cert_private_key": "private-key",
        }
    )

    assert valid is False
    assert "oa_appsecret" in message


@pytest.mark.anyio
async def test_auth_url_stores_single_use_scan_authority_and_uses_wechat_origin():
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    token = create_scan_token(
        "PUBLIC001",
        compute_ip_hash("127.0.0.1"),
        str(tenant_id),
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    redis.eval.side_effect = lambda *args: [0, args[-1]]
    db = AsyncMock()
    consent = MagicMock(id=uuid.uuid4())

    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth, "_cash_connector", new=AsyncMock(return_value=object())),
        patch.object(wechat_oauth, "_oauth_credentials", return_value=("wx-app", "wx-secret")),
        patch.object(wechat_oauth, "grant_consent", new=AsyncMock(return_value=consent)),
    ):
        result = await wechat_oauth.get_auth_url(
            _request(),
            wechat_oauth.WeChatOAuthStart(
                benefit_id=benefit_id,
                scan_token=token,
                consent_granted=True,
            ),
            db,
        )

    assert result["auth_url"].startswith("https://open.weixin.qq.com/connect/oauth2/authorize?")
    assert "scope=snsapi_base" in result["auth_url"]
    key, ttl, raw = redis.setex.await_args.args
    assert key.startswith("wechat-oauth:v1:")
    assert ttl == 300
    stored = json.loads(raw)
    assert stored == {
        "tenant_id": str(tenant_id),
        "benefit_id": str(benefit_id),
        "scan_token": token,
        "consent_id": str(consent.id),
        "pending_key": stored["pending_key"],
    }
    assert stored["pending_key"].startswith("wechat-oauth:pending:")
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_auth_url_reuses_the_single_outstanding_state_without_new_consent():
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    token = create_scan_token(
        "PUBLIC001",
        compute_ip_hash("127.0.0.1"),
        str(tenant_id),
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    redis.eval.return_value = [1, "a" * 64]
    redis.exists.return_value = 1
    db = AsyncMock()
    grant = AsyncMock()
    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth, "_cash_connector", new=AsyncMock(return_value=object())),
        patch.object(wechat_oauth, "_oauth_credentials", return_value=("wx-app", "wx-secret")),
        patch.object(wechat_oauth, "grant_consent", new=grant),
    ):
        result = await wechat_oauth.get_auth_url(
            _request(),
            wechat_oauth.WeChatOAuthStart(
                benefit_id=benefit_id,
                scan_token=token,
                consent_granted=True,
            ),
            db,
        )

    assert "state=" + "a" * 64 in result["auth_url"]
    grant.assert_not_awaited()
    redis.setex.assert_not_awaited()


@pytest.mark.anyio
async def test_auth_url_rate_limit_fails_before_consent_persistence():
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    token = create_scan_token(
        "PUBLIC001",
        compute_ip_hash("127.0.0.1"),
        str(tenant_id),
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    redis.eval.return_value = [2, ""]
    grant = AsyncMock()
    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth, "_cash_connector", new=AsyncMock(return_value=object())),
        patch.object(wechat_oauth, "_oauth_credentials", return_value=("wx-app", "wx-secret")),
        patch.object(wechat_oauth, "grant_consent", new=grant),
        pytest.raises(HTTPException) as caught,
    ):
        await wechat_oauth.get_auth_url(
            _request(),
            wechat_oauth.WeChatOAuthStart(
                benefit_id=benefit_id,
                scan_token=token,
                consent_granted=True,
            ),
            AsyncMock(),
        )

    assert caught.value.status_code == 429
    grant.assert_not_awaited()


@pytest.mark.anyio
async def test_oauth_callback_consumes_state_and_returns_member_bound_token():
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    consumer = ConsumerProfile(id=uuid.uuid4(), tenant_id=tenant_id)
    token = create_scan_token(
        "PUBLIC001",
        "ip-hash",
        str(tenant_id),
        consumer_id=str(consumer.id),
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    consent_id = uuid.uuid4()
    redis.get.return_value = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "benefit_id": str(benefit_id),
            "scan_token": token,
            "consent_id": str(consent_id),
            "pending_key": "wechat-oauth:pending:test",
        }
    )
    redis.incr.return_value = 1
    redis.set.return_value = True
    db = AsyncMock()
    db.scalar.return_value = MagicMock(id=consent_id)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"openid": "openid-authoritative"}
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None

    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth, "_cash_connector", new=AsyncMock(return_value=object())),
        patch.object(wechat_oauth, "_oauth_credentials", return_value=("wx-app", "wx-secret")),
        patch.object(wechat_oauth, "_bind_openid", new=AsyncMock(return_value=consumer)),
        patch.object(wechat_oauth, "write_audit_log", new=AsyncMock()) as audit,
        patch.object(wechat_oauth.httpx, "AsyncClient", return_value=context),
        patch.object(wechat_oauth.settings, "h5_public_url", "https://h5.example.com"),
    ):
        redirect = await wechat_oauth.oauth_callback(_request(), "oauth-code", "one-time-state", db)

    assert redirect.status_code == 303
    assert redirect.headers["location"].startswith("https://h5.example.com/c/PUBLIC001#")
    rebound_token = parse_qs(urlsplit(redirect.headers["location"]).fragment)["scan_token"][0]
    rebound = verify_scan_token(rebound_token)
    assert rebound is not None
    assert rebound["consumer_id"] == str(consumer.id)
    audit.assert_awaited_once()
    redis.get.assert_awaited_once_with("wechat-oauth:v1:one-time-state")
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_oauth_callback_rejects_replayed_state_before_database_access():
    redis = AsyncMock()
    redis.incr.return_value = 1
    redis.get.return_value = None
    db = AsyncMock()
    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        pytest.raises(HTTPException) as caught,
    ):
        await wechat_oauth.oauth_callback(_request(), "oauth-code", "used-state", db)

    assert caught.value.status_code == 401
    db.execute.assert_not_awaited()
    db.scalar.assert_not_awaited()


@pytest.mark.anyio
async def test_oauth_callback_requires_the_exact_active_consent_before_exchange():
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    token = create_scan_token(
        "PUBLIC001",
        "ip-hash",
        str(tenant_id),
        scan_event_id=str(uuid.uuid4()),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    redis.incr.return_value = 1
    redis.set.return_value = True
    redis.get.return_value = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "benefit_id": str(benefit_id),
            "scan_token": token,
            "consent_id": str(uuid.uuid4()),
            "pending_key": "wechat-oauth:pending:test",
        }
    )
    db = AsyncMock()
    db.scalar.return_value = None
    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth.httpx, "AsyncClient") as http_client,
        pytest.raises(HTTPException) as caught,
    ):
        await wechat_oauth.oauth_callback(_request(), "oauth-code", "b" * 64, db)

    assert caught.value.status_code == 403
    http_client.assert_not_called()
    redis.delete.assert_awaited_with("wechat-oauth:v1:" + "b" * 64 + ":processing")


@pytest.mark.anyio
async def test_openid_binding_persists_only_tenant_bound_hash_and_ciphertext():
    tenant_id = uuid.uuid4()
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    db.flush = AsyncMock()

    consumer = await wechat_oauth._bind_openid(
        db,
        tenant_id,
        {"consumer_id": ""},
        "openid-authoritative",
    )

    assert consumer.wechat_openid_hash == hash_wechat_openid(tenant_id, "openid-authoritative")
    assert consumer.wechat_openid_ciphertext != b"openid-authoritative"
    assert (
        decrypt_wechat_openid(
            tenant_id,
            consumer.id,
            consumer.wechat_openid_ciphertext,
            consumer.wechat_openid_nonce,
            consumer.wechat_openid_key_id,
        )
        == "openid-authoritative"
    )
