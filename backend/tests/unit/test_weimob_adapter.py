"""微盟适配器与协议工具单元测试（不发真实 HTTP，全部 monkeypatch 协议层）。"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import app.utils.weimob as weimob_protocol
from app.services.connectors.weimob import WeimobAdapter
from app.utils.weimob import WeimobAPIError


class _RuntimeConnector:
    """runtime 视图替身：config 已包含合并后的 secrets。"""

    def __init__(self, config: dict):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.name = "weimob-test"
        self.connector_type = "weimob"
        self.config = config
        self.secrets_encrypted = None
        self.enabled = True


def _fresh_token_config(**overrides) -> dict:
    config = {
        "client_id": "client-id-1",
        "client_secret": "client-secret-1",
        "shop_id": "shop-1",
        "shop_type": "public_account_id",
        "vid": "6000014039354",
        "vid_type": "2",
        "access_token": "at-valid",
        "token_expires_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
    }
    config.update(overrides)
    return config


def _benefit_config(**overrides) -> dict:
    payload = {
        "coupon_id": "1763208",
        "phone": "13800138000",
        "idempotency_key": "claim-idempotency-1",
    }
    payload.update(overrides)
    return payload


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _sequenced_calls(responses: list):
    """按调用顺序返回预设响应的 recorder，记录 (args, kwargs)。"""
    calls: list = []
    queue = list(responses)

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        raise AssertionError("unexpected extra protocol call")

    return calls, fake


def _envelope_body(event: str, msg_body: dict, *, topic: str = "weimob_crm.coupon", sign: str = "x") -> bytes:
    return json.dumps(
        {"id": "push-1", "topic": topic, "event": event, "bosId": "bos-1", "sign": sign, "msgBody": msg_body}
    ).encode()


# ── 协议工具 ────────────────────────────────────────────────


def test_verify_push_signature_matches_compact_and_sorted_forms():
    msg_body = {"wid": 4590952, "code": "WM-1"}
    compact = json.dumps(msg_body, separators=(",", ":"), ensure_ascii=False)
    sorted_form = json.dumps(msg_body, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    kwargs = dict(client_id="cid", client_secret="sec", msg_id="push-1", msg_body=msg_body)
    assert weimob_protocol.verify_push_signature(sign=_md5(f"cidpush-1{compact}sec"), **kwargs) is True
    assert weimob_protocol.verify_push_signature(sign=_md5(f"cidpush-1{sorted_form}sec"), **kwargs) is True
    # 大小写不敏感（MD5 hex 的大小写表示等价）
    assert weimob_protocol.verify_push_signature(sign=_md5(f"cidpush-1{compact}sec").upper(), **kwargs) is True
    assert (
        weimob_protocol.verify_push_signature(
            sign=_md5(f"cidpush-1{compact}sec"), **{**kwargs, "client_secret": "wrong"}
        )
        is False
    )
    assert (
        weimob_protocol.verify_push_signature(
            sign=_md5(f"cidpush-1{compact}sec"), **{**kwargs, "msg_body": {"code": "WM-2"}}
        )
        is False
    )


def test_verify_push_signature_accepts_string_msg_body():
    raw = '{"code":"WM-1"}'
    sign = _md5(f"cidpush-1{raw}sec")
    assert (
        weimob_protocol.verify_push_signature(
            client_id="cid", client_secret="sec", msg_id="push-1", msg_body=raw, sign=sign
        )
        is True
    )
    assert (
        weimob_protocol.verify_push_signature(
            client_id="cid", client_secret="sec", msg_id=None, msg_body=raw, sign=sign
        )
        is False
    )


def test_parse_push_payload_json_only():
    msg, sign = weimob_protocol.parse_push_payload(_envelope_body("receiveCoupon", {}, sign="S1"))
    assert msg["event"] == "receiveCoupon"
    assert sign == "S1"
    assert weimob_protocol.parse_push_payload(b"not-json")[0] is None
    assert weimob_protocol.parse_push_payload(b"")[0] is None
    assert weimob_protocol.parse_push_payload(b"[1,2]")[0] is None


def test_push_event_helpers():
    envelope = {"topic": "weimob_crm.coupon", "event": "receiveCoupon", "msgBody": {"code": "C1"}}
    assert weimob_protocol.push_event_fields(envelope) == ("weimob_crm.coupon", "receiveCoupon")
    assert weimob_protocol.push_event_data(envelope) == {"code": "C1"}
    # msgBody 为 JSON 字符串时同样解出
    str_body = {"event": "consumeCoupon", "msgBody": json.dumps({"code": "C2"})}
    assert weimob_protocol.push_event_data(str_body) == {"code": "C2"}
    assert weimob_protocol.push_event_fields({}) == ("", "")
    assert weimob_protocol.push_event_data({}) == {}


def test_token_expired_branches():
    assert weimob_protocol.token_expired(None) is True
    assert weimob_protocol.token_expired("not-a-date") is True
    # 默认缓冲 24h：还有 3 天 → 有效；还剩 23h → 已进刷新窗口
    assert weimob_protocol.token_expired((datetime.now(UTC) + timedelta(days=3)).isoformat()) is False
    assert weimob_protocol.token_expired((datetime.now(UTC) + timedelta(hours=23)).isoformat()) is True
    assert (
        weimob_protocol.token_expired((datetime.now(UTC) + timedelta(hours=23)).isoformat(), buffer_seconds=3600)
        is False
    )
    assert "T" in weimob_protocol.token_expiry_iso(604799)


# ── 适配器 ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_validate_config_requires_full_store_credentials():
    adapter = WeimobAdapter()
    ok, _ = await adapter.validate_config(
        {
            "client_id": "a",
            "client_secret": "b",
            "shop_id": "s",
            "shop_type": "public_account_id",
            "vid": "1",
            "vid_type": "2",
        }
    )
    assert ok
    ok, err = await adapter.validate_config({"client_id": "a", "client_secret": "b"})
    assert not ok and "shop_id" in err and "vid" in err
    ok, err = await adapter.validate_config({**_fresh_token_config(), "client_id": "x" * 65})
    assert not ok and "client_id too long" in err


@pytest.mark.anyio
async def test_deliver_missing_inputs_fails_fast():
    adapter = WeimobAdapter()
    connector = _RuntimeConnector(_fresh_token_config())
    result = await adapter.deliver(connector, "consumer-1", _benefit_config(coupon_id=""))
    assert result.status == "failed"
    assert "coupon_id" in result.message

    result = await adapter.deliver(connector, "consumer-1", _benefit_config(phone=""))
    assert result.status == "failed"
    assert "phone" in result.message


@pytest.mark.anyio
async def test_deliver_returns_pending_when_token_expired(monkeypatch):
    calls, fake = _sequenced_calls([{}])
    monkeypatch.setattr(weimob_protocol, "call_weimob_api", fake)
    adapter = WeimobAdapter()
    connector = _RuntimeConnector(_fresh_token_config(access_token="", token_expires_at=None))
    result = await adapter.deliver(connector, "consumer-1", _benefit_config())
    assert result.status == "pending"
    assert result.message == "token_refresh_required"
    assert calls == []


@pytest.mark.anyio
async def test_deliver_success_resolves_wid_and_uses_coupon_code():
    calls, fake = _sequenced_calls(
        [
            {"successList": [{"wid": 4590952, "phone": "13800138000"}]},
            {
                "successCount": 1,
                "failedCount": 0,
                "couponResultList": [{"codes": ["WM-CODE-9"], "isSuccess": True, "wid": 4590952}],
            },
        ]
    )
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as mp:
        mp.setattr(weimob_protocol, "call_weimob_api", fake)
        adapter = WeimobAdapter()
        result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "consumer-1", _benefit_config())
    assert result.status == "success"
    assert result.external_id == "WM-CODE-9"
    assert result.external_data["codes"] == ["WM-CODE-9"]
    # 第一跳：customer/import 以手机号换 wid
    assert calls[0][1]["path"] == weimob_protocol.CUSTOMER_IMPORT_PATH
    assert calls[0][1]["json_body"]["phone"] == "13800138000"
    # 第二跳：coupon/receive，requestId = claim 幂等锚点，数值参数已整型化
    receive = calls[1][1]
    assert receive["path"] == weimob_protocol.COUPON_RECEIVE_PATH
    assert receive["json_body"]["wid"] == 4590952
    assert receive["json_body"]["scene"] == weimob_protocol.API_SCENE
    assert receive["json_body"]["couponNums"][0]["requestId"] == "claim-idempotency-1"
    assert receive["json_body"]["couponNums"][0]["couponTemplateId"] == 1763208


@pytest.mark.anyio
async def test_deliver_fails_when_wid_unresolved(monkeypatch):
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as mp:
        calls, fake = _sequenced_calls([{"successList": [], "failedList": [{"errMsg": "invalid phone"}]}])
        mp.setattr(weimob_protocol, "call_weimob_api", fake)
        adapter = WeimobAdapter()
        result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == "failed"
    assert "wid resolution failed" in result.message
    assert "invalid phone" in result.message


@pytest.mark.anyio
async def test_deliver_fails_when_coupon_rejected(monkeypatch):
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as mp:
        _, fake = _sequenced_calls(
            [
                {"successList": [{"wid": 1}]},
                {
                    "couponResultList": [
                        {"codes": [], "isSuccess": False, "errCode": 40001, "errMsg": "coupon exhausted"}
                    ]
                },
            ]
        )
        mp.setattr(weimob_protocol, "call_weimob_api", fake)
        adapter = WeimobAdapter()
        result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == "failed"
    assert "coupon exhausted" in result.message


@pytest.mark.anyio
async def test_deliver_falls_back_to_idempotency_key(monkeypatch):
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as mp:
        _, fake = _sequenced_calls(
            [
                {"successList": [{"wid": 1}]},
                {"couponResultList": [{"codes": [], "isSuccess": True}]},
            ]
        )
        mp.setattr(weimob_protocol, "call_weimob_api", fake)
        adapter = WeimobAdapter()
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
        raise WeimobAPIError(status_code=status_code, code=8000103, message="m")

    monkeypatch.setattr(weimob_protocol, "call_weimob_api", boom)
    adapter = WeimobAdapter()
    result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == expected


@pytest.mark.anyio
async def test_deliver_timeout_is_pending(monkeypatch):
    async def boom(**kwargs):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(weimob_protocol, "call_weimob_api", boom)
    adapter = WeimobAdapter()
    result = await adapter.deliver(_RuntimeConnector(_fresh_token_config()), "c", _benefit_config())
    assert result.status == "pending"
    assert result.external_data["reason"] == "ambiguous_provider_outcome"


@pytest.mark.anyio
async def test_reconcile_confirms_or_rejects(monkeypatch):
    adapter = WeimobAdapter()
    conn = _RuntimeConnector(_fresh_token_config())

    monkeypatch.setattr(weimob_protocol, "call_weimob_api", _make_api({"couponList": [{"code": "WM-1"}]}))
    result = await adapter.reconcile(conn, "WM-1")
    assert result.status == "success"

    monkeypatch.setattr(weimob_protocol, "call_weimob_api", _make_api({"couponList": []}))
    result = await adapter.reconcile(conn, "WM-1")
    assert result.status == "failed"

    monkeypatch.setattr(weimob_protocol, "call_weimob_api", _make_api({"unknown": "shape"}))
    result = await adapter.reconcile(conn, "WM-1")
    assert result.status == "pending"

    async def boom(**kwargs):
        raise WeimobAPIError(status_code=503, code=None, message="m")

    monkeypatch.setattr(weimob_protocol, "call_weimob_api", boom)
    result = await adapter.reconcile(conn, "WM-1")
    assert result.status == "pending"


def _make_api(value):
    async def fake(**kwargs):
        return value

    return fake


@pytest.mark.anyio
async def test_parse_callback_grant_event_is_settleable():
    adapter = WeimobAdapter()
    body = _envelope_body("receiveCoupon", {"code": "WM-CODE-1", "wid": 1})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.external_id == "WM-CODE-1"
    assert result.status == "success"

    body = _envelope_body("receiveCoupon", {"code": "WM-CODE-2", "status": "failed"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.external_id == "WM-CODE-2"
    assert result.status == "failed"

    # 无法定位券码的发放事件不结算
    body = _envelope_body("receiveCoupon", {"wid": 1})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"


@pytest.mark.anyio
async def test_parse_callback_consume_event_returns_wallet_transition():
    adapter = WeimobAdapter()
    body = _envelope_body("consumeCoupon", {"code": "WM-CODE-9", "orderNo": "O1"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"
    assert result.coupon_transition == "external_consume"
    assert result.external_coupon_ref == "WM-CODE-9"

    body = _envelope_body("consumeCoupon", {})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"
    assert result.coupon_transition is None


@pytest.mark.anyio
async def test_parse_callback_unrelated_and_invalid_events_are_ignored():
    adapter = WeimobAdapter()
    body = _envelope_body("cancelCoupon", {"code": "WM-CODE-3"})
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"
    assert result.coupon_transition is None

    body = _envelope_body("orderPaid", {"orderNo": "O2"}, topic="weimob_ec.order")
    result = await adapter.parse_callback(_RuntimeConnector({}), body, {})
    assert result.status == "ignored"

    result = await adapter.parse_callback(_RuntimeConnector({}), b"not-json", {})
    assert result.status == "ignored"


@pytest.mark.anyio
async def test_verify_callback_checks_envelope_signature():
    adapter = WeimobAdapter()
    connector = _RuntimeConnector({"client_id": "cid", "client_secret": "sec"})
    msg_body = {"code": "WM-CODE-1"}
    compact = json.dumps(msg_body, separators=(",", ":"), ensure_ascii=False)
    sign = _md5(f"cidpush-1{compact}sec")
    body = _envelope_body("receiveCoupon", msg_body, sign=sign)

    assert await adapter.verify_callback(connector, body, {}) is True
    tampered = body.replace(b"push-1", b"push-2")
    assert await adapter.verify_callback(connector, tampered, {}) is False
    assert await adapter.verify_callback(_RuntimeConnector({}), body, {}) is False
    assert await adapter.verify_callback(connector, b"", {}) is False


@pytest.mark.anyio
async def test_callback_ack_payload_matches_weimob_contract():
    adapter = WeimobAdapter()
    ack = await adapter.callback_ack_payload(None)
    assert ack == {"code": {"errcode": 0, "errmsg": "success"}}


@pytest.mark.anyio
async def test_base_adapter_default_ack_payload_is_none():
    from app.services.connectors.base import BaseConnectorAdapter, CallbackResult

    class _Minimal(BaseConnectorAdapter):
        async def sync_stock(self, connector):
            return -1

        async def deliver(self, connector, consumer_id, benefit_config):
            return DeliveryResultStub()

        async def parse_callback(self, connector, request_body, headers):
            return CallbackResult(status="ignored")

        async def validate_config(self, config):
            return True, ""

    class DeliveryResultStub:
        status = "success"

    assert await _Minimal().callback_ack_payload(CallbackResult(status="ignored")) is None


@pytest.mark.anyio
async def test_test_connection_uses_client_credentials(monkeypatch):
    calls = []

    async def fake_fetch_token(**kwargs):
        calls.append(kwargs)
        return {"access_token": "at", "expires_in": 604799}

    monkeypatch.setattr(weimob_protocol, "fetch_token", fake_fetch_token)
    adapter = WeimobAdapter()
    ok, msg = await adapter.test_connection(
        {"client_id": "a", "client_secret": "b", "shop_id": "s", "shop_type": "public_account_id"}
    )
    assert ok and msg == "ok"
    # fetch_token 固定 client_credentials 模式（自用型正式店铺无其他 grant）
    assert calls[0]["client_id"] == "a"
    assert calls[0]["shop_id"] == "s"
    assert calls[0]["shop_type"] == "public_account_id"

    async def boom(**kwargs):
        raise WeimobAPIError(status_code=401, code=None, message="bad credential")

    monkeypatch.setattr(weimob_protocol, "fetch_token", boom)
    ok, msg = await adapter.test_connection(
        {"client_id": "a", "client_secret": "b", "shop_id": "s", "shop_type": "public_account_id"}
    )
    assert not ok
    ok, _ = await adapter.test_connection({"client_id": "a"})
    assert not ok


@pytest.mark.anyio
async def test_sync_stock_is_not_applicable():
    adapter = WeimobAdapter()
    assert await adapter.sync_stock(_RuntimeConnector({})) == -1
