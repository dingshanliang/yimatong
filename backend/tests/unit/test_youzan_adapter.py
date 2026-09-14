"""有赞适配器与协议工具单元测试（不发真实 HTTP，全部 monkeypatch 协议层）。"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import app.utils.youzan as youzan_protocol
from app.services.connectors.youzan import YouzanAdapter
from app.utils.youzan import YouzanAPIError


class _RuntimeConnector:
    """runtime 视图替身：config 已包含合并后的 secrets。"""

    def __init__(self, config: dict):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.name = "youzan-test"
        self.connector_type = "youzan"
        self.config = config
        self.secrets_encrypted = None
        self.enabled = True


def _fresh_token_config(**overrides) -> dict:
    config = {
        "client_id": "client-id-1",
        "client_secret": "client-secret-1",
        "access_token": "at-valid",
        "token_expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
    }
    config.update(overrides)
    return config


def _benefit_config(**overrides) -> dict:
    payload = {
        "coupon_id": "YZ-COUPON-TEMPLATE-1",
        "openid": "openid-consumer-1",
        "idempotency_key": "claim-idempotency-1",
    }
    payload.update(overrides)
    return payload


# ── 协议工具 ────────────────────────────────────────────────


def test_sign_request_sorts_keys_and_wraps_secret():
    from app.utils.youzan import sign_request

    a = sign_request({"b": "2", "a": "1"}, "sec")
    b = sign_request({"a": "1", "b": "2"}, "sec")
    assert a == b
    assert len(a) == 32
    assert sign_request({"a": "1"}, "other") != a


def test_verify_push_signature_matches_and_rejects():
    from app.utils.youzan import verify_push_signature

    msg = '{"type":"coupon_take"}'
    sign = verify_push_signature.__globals__["_md5_upper"](f"sec{msg}sec")
    assert verify_push_signature(msg, sign, "sec") is True
    assert verify_push_signature(msg, "0" * 32, "sec") is False
    assert verify_push_signature(msg, sign, "wrong") is False


def test_parse_push_payload_supports_json_and_form():
    from app.utils.youzan import parse_push_payload

    msg, raw, sign = parse_push_payload(b'{"msg": "{\\"type\\": \\"t\\"}", "sign": "ABC"}')
    assert msg == {"type": "t"}
    assert sign == "ABC"

    msg2, raw2, sign2 = parse_push_payload(b"msg=%7B%22type%22%3A%22t%22%7D&sign=DEF")
    assert msg2 == {"type": "t"}
    assert sign2 == "DEF"

    assert parse_push_payload(b"")[0] is None
    assert parse_push_payload(b"msg=not-json&sign=X")[0] is None


def test_token_expired_branches():
    from app.utils.youzan import token_expired, token_expiry_iso

    assert token_expired(None) is True
    assert token_expired("not-a-date") is True
    fresh = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert token_expired(fresh) is False
    inside_buffer = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    assert token_expired(inside_buffer) is True
    assert token_expired(inside_buffer, buffer_seconds=60) is False
    assert token_expiry_iso(60).endswith(("+00:00", "Z")) or "T" in token_expiry_iso(60)


def test_push_event_helpers():
    from app.utils.youzan import push_event_data, push_event_type

    msg = {"type": "coupon_take_succeeded", "data": {"record_id": 1}}
    assert push_event_type(msg) == "coupon_take_succeeded"
    assert push_event_data(msg) == {"record_id": 1}
    assert push_event_type({"data": {}}) == ""
    assert push_event_data({"msg": {"k": 1}}) == {"k": 1}


# ── 适配器 ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_validate_config_requires_client_credentials():
    adapter = YouzanAdapter()
    ok, _ = await adapter.validate_config({"client_id": "a", "client_secret": "b"})
    assert ok
    ok, err = await adapter.validate_config({"client_id": "a"})
    assert not ok and "client_secret" in err
    ok, err = await adapter.validate_config({"client_id": "x" * 65, "client_secret": "b"})
    assert not ok


@pytest.mark.anyio
async def test_deliver_missing_inputs_fails_fast():
    adapter = YouzanAdapter()
    connector = _RuntimeConnector(_fresh_token_config())
    result = await adapter.deliver(connector, "consumer-1", _benefit_config(coupon_id=""))
    assert result.status == "failed"
    assert "coupon_id" in result.message


@pytest.mark.anyio
async def test_deliver_returns_pending_when_token_expired(monkeypatch):
    called = []
    monkeypatch.setattr(youzan_protocol, "call_youzan_api", _async_recorder(called, {}))
    adapter = YouzanAdapter()
    connector = _RuntimeConnector(_fresh_token_config(access_token="", token_expires_at=None))
    result = await adapter.deliver(connector, "consumer-1", _benefit_config())
    assert result.status == "pending"
    assert result.message == "token_refresh_required"
    assert called == []


def _async_recorder(calls, value):
    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return value

    return fake


@pytest.mark.anyio
async def test_deliver_success_uses_grant_record_id(monkeypatch):
    calls = []
    monkeypatch.setattr(
        youzan_protocol,
        "call_youzan_api",
        _async_recorder(calls, {"record_id": "YZ-GRANT-9", "status": "success"}),
    )
    adapter = YouzanAdapter()
    connector = _RuntimeConnector(_fresh_token_config())
    result = await adapter.deliver(connector, "consumer-1", _benefit_config())
    assert result.status == "success"
    assert result.external_id == "YZ-GRANT-9"
    assert calls[0][1]["method"] == youzan_protocol.COUPON_TAKE_METHOD
    assert calls[0][1]["params"]["outer_id"] == "claim-idempotency-1"


@pytest.mark.anyio
async def test_deliver_falls_back_to_idempotency_key(monkeypatch):
    monkeypatch.setattr(youzan_protocol, "call_youzan_api", _async_recorder([], {}))
    adapter = YouzanAdapter()
    result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == "success"
    assert result.external_id == "claim-idempotency-1"


@pytest.mark.parametrize(
    "status_code,expected",
    [(500, "pending"), (503, "pending"), (429, "pending"), (400, "failed"), (401, "failed")],
)
@pytest.mark.anyio
async def test_deliver_maps_error_classes(status_code, expected, monkeypatch):
    async def boom(**kwargs):
        raise YouzanAPIError(status_code=status_code, code="E", message="m")

    monkeypatch.setattr(youzan_protocol, "call_youzan_api", boom)
    adapter = YouzanAdapter()
    result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == expected


@pytest.mark.anyio
async def test_deliver_timeout_is_pending(monkeypatch):
    async def boom(**kwargs):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(youzan_protocol, "call_youzan_api", boom)
    adapter = YouzanAdapter()
    result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == "pending"
    assert result.external_data["reason"] == "ambiguous_provider_outcome"


@pytest.mark.anyio
async def test_reconcile_confirms_or_rejects(monkeypatch):
    adapter = YouzanAdapter()
    conn = _RuntimeConnector(_fresh_token_config())

    monkeypatch.setattr(youzan_protocol, "call_youzan_api", _async_recorder([], {"records": [{"id": 1}]}))
    result = await adapter.reconcile(conn, "YZ-1")
    assert result.status == "success"

    monkeypatch.setattr(youzan_protocol, "call_youzan_api", _async_recorder([], {"records": [], "total": 0}))
    result = await adapter.reconcile(conn, "YZ-1")
    assert result.status == "failed"

    monkeypatch.setattr(youzan_protocol, "call_youzan_api", _async_recorder([], {"unknown": "shape"}))
    result = await adapter.reconcile(conn, "YZ-1")
    assert result.status == "pending"

    async def boom(**kwargs):
        raise YouzanAPIError(status_code=503, code=None, message="m")

    monkeypatch.setattr(youzan_protocol, "call_youzan_api", boom)
    result = await adapter.reconcile(conn, "YZ-1")
    assert result.status == "pending"


def _push_body(event_type: str, data: dict) -> bytes:
    import json
    from urllib.parse import quote

    msg = json.dumps({"type": event_type, "data": data}, separators=(",", ":"))
    return f"msg={quote(msg)}".encode()


@pytest.mark.anyio
async def test_parse_callback_grant_event_is_settleable():
    adapter = YouzanAdapter()
    body = _push_body("coupon_take_succeeded", {"record_id": "YZ-GRANT-1", "status": "success"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.external_id == "YZ-GRANT-1"
    assert result.status == "success"

    body = _push_body("coupon_grant_failed", {"record_id": "YZ-GRANT-2", "status": "failed"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.external_id == "YZ-GRANT-2"
    assert result.status == "failed"


@pytest.mark.anyio
async def test_parse_callback_consume_event_returns_wallet_transition():
    adapter = YouzanAdapter()
    body = _push_body("coupon_consume_succeeded", {"coupon_no": "YZ-CODE-9"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"
    assert result.coupon_transition == "external_consume"
    assert result.external_coupon_ref == "YZ-CODE-9"

    body = _push_body("coupon_consume_succeeded", {})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"
    assert result.coupon_transition is None


@pytest.mark.anyio
async def test_parse_callback_unknown_event_is_ignored():
    adapter = YouzanAdapter()
    body = _push_body("trade_paid", {"tid": "T1"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"
    assert result.coupon_transition is None


@pytest.mark.anyio
async def test_verify_callback_checks_secret_signature():
    import json
    from urllib.parse import quote

    adapter = YouzanAdapter()
    connector = _RuntimeConnector({"client_secret": "sec-1"})
    msg = json.dumps({"type": "coupon_take_succeeded", "data": {}}, separators=(",", ":"))
    sign = youzan_protocol.verify_push_signature.__globals__["_md5_upper"](f"sec-1{msg}sec-1")
    body = f"msg={quote(msg)}&sign={sign}".encode()

    assert await adapter.verify_callback(connector, body, {}) is True
    tampered = body.replace(b"sign=", b"sign=X")
    assert await adapter.verify_callback(connector, tampered, {}) is False


@pytest.mark.anyio
async def test_verify_callback_rejects_missing_secret_or_payload():
    adapter = YouzanAdapter()
    assert await adapter.verify_callback(_RuntimeConnector({}), b"msg={}&sign=1", {}) is False
    assert await adapter.verify_callback(_RuntimeConnector({"client_secret": "s"}), b"", {}) is False


@pytest.mark.anyio
async def test_test_connection_uses_silent_grant(monkeypatch):
    calls = []

    async def fake_fetch_token(**kwargs):
        calls.append(kwargs)
        return {"access_token": "at"}

    monkeypatch.setattr(youzan_protocol, "fetch_token", fake_fetch_token)
    adapter = YouzanAdapter()
    ok, msg = await adapter.test_connection({"client_id": "a", "client_secret": "b"})
    assert ok and msg == "ok"
    assert calls[0]["grant_type"] == youzan_protocol.GRANT_TYPE_SILENT

    async def boom(**kwargs):
        raise YouzanAPIError(status_code=401, code=None, message="bad credential")

    monkeypatch.setattr(youzan_protocol, "fetch_token", boom)
    ok, msg = await adapter.test_connection({"client_id": "a", "client_secret": "b"})
    assert not ok
    ok, _ = await adapter.test_connection({"client_id": "a"})
    assert not ok


@pytest.mark.anyio
async def test_sync_stock_is_not_applicable():
    adapter = YouzanAdapter()
    assert await adapter.sync_stock(_RuntimeConnector({})) == -1
