import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v1 import wechat_oauth
from app.services.connectors.wechat_pay_transfer import WeChatPayTransferAdapter
from app.services.scan_token import create_scan_token, verify_scan_token
from app.utils.client_ip import compute_ip_hash


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
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    redis.eval.side_effect = lambda *args: [0, args[-1]]
    db = AsyncMock()
    consent_id = uuid.uuid4()
    policy = {
        "purpose": "wechat_cash_payout",
        "policy_version": "current-v2",
        "policy_digest": "a" * 64,
    }
    grant = AsyncMock(return_value={"consent_id": consent_id, "status": "granted"})

    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth, "_cash_connector", new=AsyncMock(return_value=object())),
        patch.object(wechat_oauth, "_oauth_credentials", return_value=("wx-app", "wx-secret")),
        patch.object(wechat_oauth, "get_current_consumer_policy", new=AsyncMock(return_value=policy)) as current,
        patch.object(wechat_oauth, "grant_consumer_consent_authority", new=grant),
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
        "consent_id": str(consent_id),
        "pending_key": stored["pending_key"],
    }
    assert stored["pending_key"].startswith("wechat-oauth:pending:")
    current.assert_awaited_once_with(db, tenant_id, "wechat_cash_payout")
    grant.assert_awaited_once()
    assert grant.await_args.kwargs["purpose"] == "wechat_cash_payout"
    assert grant.await_args.kwargs["expected_version"] == "current-v2"
    assert grant.await_args.kwargs["expected_digest"] == "a" * 64
    assert grant.await_args.kwargs["public_id"] == "PUBLIC001"
    assert grant.await_args.kwargs["idempotency_key"].startswith("wechat-oauth:")
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
        scan_time=datetime.now(UTC).isoformat(),
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
        patch.object(wechat_oauth, "grant_consumer_consent_authority", new=grant),
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
        scan_time=datetime.now(UTC).isoformat(),
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
        patch.object(wechat_oauth, "grant_consumer_consent_authority", new=grant),
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
async def test_auth_url_rolls_back_consent_when_oauth_state_cannot_be_persisted():
    tenant_id = uuid.uuid4()
    token = create_scan_token(
        "PUBLIC001",
        compute_ip_hash("127.0.0.1"),
        str(tenant_id),
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
    )
    redis = AsyncMock()
    redis.eval.side_effect = lambda *args: [0, args[-1]]
    redis.setex.side_effect = RuntimeError("redis unavailable")
    db = AsyncMock()
    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(wechat_oauth, "_cash_connector", new=AsyncMock(return_value=object())),
        patch.object(wechat_oauth, "_oauth_credentials", return_value=("wx-app", "wx-secret")),
        patch.object(
            wechat_oauth,
            "get_current_consumer_policy",
            new=AsyncMock(
                return_value={
                    "purpose": "wechat_cash_payout",
                    "policy_version": "current-v2",
                    "policy_digest": "a" * 64,
                }
            ),
        ),
        patch.object(
            wechat_oauth,
            "grant_consumer_consent_authority",
            new=AsyncMock(return_value={"consent_id": uuid.uuid4(), "status": "granted"}),
        ),
        pytest.raises(RuntimeError, match="redis unavailable"),
    ):
        await wechat_oauth.get_auth_url(
            _request(),
            wechat_oauth.WeChatOAuthStart(benefit_id=uuid.uuid4(), scan_token=token, consent_granted=True),
            db,
        )

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()
    redis.delete.assert_awaited_once()


@pytest.mark.anyio
async def test_oauth_callback_consumes_state_and_returns_member_bound_token():
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    consumer_id = uuid.uuid4()
    token = create_scan_token(
        "PUBLIC001",
        "ip-hash",
        str(tenant_id),
        consumer_id=str(consumer_id),
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
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
        patch.object(
            wechat_oauth,
            "get_current_consumer_policy",
            new=AsyncMock(
                return_value={
                    "purpose": "wechat_cash_payout",
                    "policy_version": "current-v2",
                    "policy_digest": "a" * 64,
                }
            ),
        ),
        patch.object(
            wechat_oauth,
            "get_consumer_consent_receipt_status",
            new=AsyncMock(
                return_value={
                    "status": "granted",
                    "purpose": "wechat_cash_payout",
                    "policy_version": "current-v2",
                    "policy_digest": "a" * 64,
                }
            ),
        ),
        patch.object(
            wechat_oauth,
            "bind_wechat_oauth_consumer_authority",
            new=AsyncMock(return_value={"outcome": "bound", "consumer_id": consumer_id, "consent_id": consent_id}),
        ) as bind,
        patch.object(wechat_oauth.httpx, "AsyncClient", return_value=context),
        patch.object(wechat_oauth.settings, "h5_public_url", "https://h5.example.com"),
    ):
        redirect = await wechat_oauth.oauth_callback(_request(), "oauth-code", "one-time-state", db)

    assert redirect.status_code == 303
    assert redirect.headers["location"].startswith("https://h5.example.com/c/PUBLIC001#")
    rebound_token = parse_qs(urlsplit(redirect.headers["location"]).fragment)["scan_token"][0]
    rebound = verify_scan_token(rebound_token)
    assert rebound is not None
    assert rebound["consumer_id"] == str(consumer_id)
    bind.assert_awaited_once()
    assert bind.await_args.kwargs["token_consumer_id"] == consumer_id
    assert bind.await_args.kwargs["openid"] == "openid-authoritative"
    redis.get.assert_awaited_once_with("wechat-oauth:v1:one-time-state")
    db.commit.assert_awaited_once()


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
        scan_time=datetime.now(UTC).isoformat(),
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
    with (
        patch.object(wechat_oauth, "get_redis_pool", new=AsyncMock(return_value=redis)),
        patch.object(wechat_oauth, "set_session_tenant_context", new=AsyncMock()),
        patch.object(
            wechat_oauth,
            "get_current_consumer_policy",
            new=AsyncMock(
                return_value={
                    "purpose": "wechat_cash_payout",
                    "policy_version": "current-v2",
                    "policy_digest": "a" * 64,
                }
            ),
        ),
        patch.object(
            wechat_oauth,
            "get_consumer_consent_receipt_status",
            new=AsyncMock(
                return_value={
                    "status": "withdrawn",
                    "purpose": "wechat_cash_payout",
                    "policy_version": "current-v2",
                    "policy_digest": "a" * 64,
                }
            ),
        ),
        patch.object(wechat_oauth.httpx, "AsyncClient") as http_client,
        pytest.raises(HTTPException) as caught,
    ):
        await wechat_oauth.oauth_callback(_request(), "oauth-code", "b" * 64, db)

    assert caught.value.status_code == 403
    http_client.assert_not_called()
    redis.delete.assert_awaited_with("wechat-oauth:v1:" + "b" * 64 + ":processing")
