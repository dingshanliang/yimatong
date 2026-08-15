"""EPIC-19 单元测试：事件总线、权限映射、签名生成。"""

import hashlib
import hmac
import uuid

import pytest

from app.core.event_bus import _EventBus
from app.services import webhook_sender
from app.services.connectors.secrets import encrypt_secrets
from app.services.webhook import build_api_key_lifecycle_material, recover_api_key_secret
from app.services.webhook_sender import (
    build_envelope,
    canonical_json_bytes,
    compute_signature,
    resolve_public_webhook_addresses,
    should_retry,
    validate_webhook_url,
)
from app.utils.auth_rbac import (
    ROLE_PERMISSIONS,
    get_permissions_for_role,
    role_has_permission,
)


class TestPermissions:
    """角色权限映射测试。"""

    def test_all_roles_have_permissions(self):
        for role in ROLE_PERMISSIONS:
            perms = get_permissions_for_role(role)
            assert len(perms) > 0, f"Role {role} has no permissions"

    def test_data_reader_cannot_write(self):
        assert not role_has_permission("data_reader", "coupon:issue")
        assert not role_has_permission("data_reader", "campaign:status")
        assert not role_has_permission("full_access", "campaign:status")

    def test_coupon_operator_can_issue(self):
        assert role_has_permission("coupon_operator", "coupon:issue")
        assert role_has_permission("coupon_operator", "scan:list")

    def test_full_access_has_all(self):
        all_perms = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms.update(perms)
        for perm in all_perms:
            assert role_has_permission("full_access", perm), f"full_access missing {perm}"

    def test_invalid_role_returns_empty(self):
        assert get_permissions_for_role("nonexistent") == []
        assert not role_has_permission("nonexistent", "scan:list")


class TestApiKeyLifecycleMaterial:
    def test_derives_replayable_hmac_material_and_encrypted_secret(self):
        arguments = {
            "tenant_id": uuid.UUID("00000000-0000-4000-8000-000000000001"),
            "actor_id": uuid.UUID("00000000-0000-4000-8000-000000000002"),
            "action": "issue",
            "target_id": None,
            "idempotency_key": "11111111-1111-4111-8111-111111111111",
            "payload": {
                "expires_at": "2027-01-02T03:04:05+00:00",
                "name": "ERP 同步",
                "permanent_reason": None,
                "role": "data_reader",
            },
            "candidate_id": uuid.UUID("00000000-0000-4000-8000-000000000003"),
        }

        first = build_api_key_lifecycle_material(**arguments)
        retried = build_api_key_lifecycle_material(**arguments)
        changed = build_api_key_lifecycle_material(
            **{**arguments, "payload": {**arguments["payload"], "role": "full_access"}}
        )

        assert first.idempotency_digest == "2dedbd077eb3c758086fb921a9fe8bfdb03f0fc35434dcb00f3f681b37c89d87"
        assert first.secret == retried.secret
        assert first.request_fingerprint == retried.request_fingerprint
        assert first.secret != changed.secret
        assert first.request_fingerprint != changed.request_fingerprint
        assert first.secret.startswith("ymt_") and len(first.secret) == 52
        assert first.key_prefix == first.secret[:12]
        assert first.key_digest == hashlib.sha256(first.secret.encode()).hexdigest()
        assert first.escrow_ciphertext != retried.escrow_ciphertext
        assert recover_api_key_secret(first.escrow_ciphertext, arguments["candidate_id"]) == first.secret

    def test_rejects_escrow_that_does_not_match_the_candidate_or_derived_material(self):
        candidate_id = uuid.UUID("00000000-0000-4000-8000-000000000003")
        other_id = uuid.UUID("00000000-0000-4000-8000-000000000004")
        material = build_api_key_lifecycle_material(
            tenant_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            actor_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
            action="issue",
            target_id=None,
            idempotency_key="11111111-1111-4111-8111-111111111111",
            payload={"name": "ERP", "role": "data_reader", "expires_at": "2027-01-02T03:04:05+00:00"},
            candidate_id=candidate_id,
        )
        wrong_secret = "ymt_" + "f" * 48
        wrong_secret_escrow = encrypt_secrets({"v": 1, "api_key_id": str(candidate_id), "secret": wrong_secret})

        with pytest.raises(RuntimeError, match="does not match lifecycle material"):
            recover_api_key_secret(
                wrong_secret_escrow,
                candidate_id,
                expected_prefix=material.key_prefix,
                expected_digest=material.key_digest,
            )
        with pytest.raises(RuntimeError, match="payload is invalid"):
            recover_api_key_secret(material.escrow_ciphertext, other_id)


class TestEventBus:
    """事件总线测试。"""

    @pytest.mark.anyio
    async def test_emit_calls_handler(self):
        bus = _EventBus()
        received = []

        async def handler(event_type, data, tenant_id):
            received.append((event_type, data, tenant_id))

        bus.add_handler("test.event", handler)
        await bus.emit("test.event", {"key": "value"}, "tenant-123")

        assert len(received) == 1
        assert received[0] == ("test.event", {"key": "value"}, "tenant-123")

    @pytest.mark.anyio
    async def test_handler_exception_does_not_block(self):
        bus = _EventBus()
        called = []

        async def bad_handler(event_type, data, tenant_id):
            raise RuntimeError("boom")

        async def good_handler(event_type, data, tenant_id):
            called.append(True)

        bus.add_handler("test.event", bad_handler)
        bus.add_handler("test.event", good_handler)
        await bus.emit("test.event", {}, "t1")

        assert len(called) == 1

    @pytest.mark.anyio
    async def test_no_handler_no_error(self):
        bus = _EventBus()
        await bus.emit("unknown.event", {}, "t1")  # should not raise


class TestWebhookSender:
    """Webhook 签名和信封测试。"""

    def test_build_envelope_structure(self):
        envelope = build_envelope("scan.created", {"public_id": "abc"}, "tenant-123")
        assert envelope["type"] == "scan.created"
        assert envelope["tenant_id"] == "tenant-123"
        assert envelope["data"] == {"public_id": "abc"}
        assert "id" in envelope
        assert "timestamp" in envelope

    def test_build_envelope_custom_event_id(self):
        envelope = build_envelope("test", {}, "t1", event_id="custom-id")
        assert envelope["id"] == "custom-id"

    def test_compute_signature(self):
        secret = "test_secret"
        body = b'{"type":"test"}'
        sig = compute_signature(secret, body)

        expected = hmac.new(secret.encode(), body, hashlib.sha256)
        assert sig == f"sha256={expected.hexdigest()}"

    def test_compute_signature_deterministic(self):
        body = b"test body"
        sig1 = compute_signature("secret1", body)
        sig2 = compute_signature("secret1", body)
        assert sig1 == sig2

    def test_canonical_body_and_timestamp_bound_signature_are_deterministic(self):
        first = canonical_json_bytes({"z": 1, "data": {"b": 2, "a": "中文"}})
        second = canonical_json_bytes({"data": {"a": "中文", "b": 2}, "z": 1})

        assert first == second == b'{"data":{"a":"\xe4\xb8\xad\xe6\x96\x87","b":2},"z":1}'
        assert compute_signature("secret", first, "1700000000") == compute_signature("secret", second, "1700000000")
        assert compute_signature("secret", first, "1700000000") != compute_signature("secret", first, "1700000001")

    @pytest.mark.parametrize(
        "url",
        [
            "http://hooks.example.com/path",
            "https://user:pass@hooks.example.com/path",
            "https://hooks.example.com:444/path",
            "https://hooks.example.com/path?token=secret",
            "https://hooks.example.com/path#fragment",
            "https://localhost/path",
            "https://service.local/path",
            "https://127.0.0.1/path",
            "https://[::1]/path",
            "https://hooks.example.com/\u8def\u5f84",
            "https://hooks.example.com/path with space",
        ],
    )
    def test_rejects_unsafe_webhook_url_shapes(self, url: str):
        with pytest.raises(ValueError, match="credential-free public HTTPS"):
            validate_webhook_url(url)

    @pytest.mark.anyio
    async def test_rejects_dns_answer_set_containing_private_or_rebinding_address(self, monkeypatch):
        loop = __import__("asyncio").get_running_loop()

        async def private_answer(*_args, **_kwargs):
            return [
                (2, 1, 6, "", ("93.184.216.34", 443)),
                (2, 1, 6, "", ("10.0.0.8", 443)),
            ]

        monkeypatch.setattr(loop, "getaddrinfo", private_answer)
        with pytest.raises(ValueError, match="resolve only to public"):
            await resolve_public_webhook_addresses("hooks.example.com")

    @pytest.mark.anyio
    async def test_delivery_uses_resolved_ip_pin_and_signs_exact_body(self, monkeypatch):
        captured = {}
        attempts = []

        async def resolve(hostname: str):
            assert hostname == "hooks.example.com"
            return ["93.184.216.34"]

        async def post(parsed, addresses, *, raw_body, headers):
            captured.update(parsed=parsed, addresses=addresses, raw_body=raw_body, headers=headers)
            attempts.append((raw_body, dict(headers)))
            return 204, ""

        monkeypatch.setattr(webhook_sender, "resolve_public_webhook_addresses", resolve)
        monkeypatch.setattr(webhook_sender, "_post_pinned", post)
        envelope = {
            "type": "scan.created",
            "id": "00000000-0000-4000-8000-000000000001",
            "timestamp": "2026-08-14T12:34:56.987654+00:00",
            "data": {"b": 2, "a": 1},
        }

        result = await webhook_sender.deliver("https://hooks.example.com/events", "secret", envelope)
        replay = await webhook_sender.deliver("https://hooks.example.com/events", "secret", envelope)

        assert result == replay == (204, "")
        assert attempts[0] == attempts[1]
        assert captured["addresses"] == ["93.184.216.34"]
        assert captured["parsed"].hostname == "hooks.example.com"
        assert captured["raw_body"] == canonical_json_bytes(envelope)
        assert captured["headers"]["X-Ymt-Signature"] == compute_signature(
            "secret", captured["raw_body"], captured["headers"]["X-Ymt-Timestamp"]
        )
        assert captured["headers"]["X-Ymt-Signature-Version"] == "v1"
        assert captured["headers"]["X-Ymt-Timestamp"] == "1786710896"

    @pytest.mark.anyio
    async def test_delivery_failure_does_not_log_or_return_endpoint_url(self, monkeypatch, caplog):
        async def resolve(_hostname: str):
            return ["93.184.216.34"]

        async def fail(*_args, **_kwargs):
            raise OSError("connection secret detail")

        monkeypatch.setattr(webhook_sender, "resolve_public_webhook_addresses", resolve)
        monkeypatch.setattr(webhook_sender, "_post_pinned", fail)
        url = "https://hooks.example.com/private-path"

        status, body = await webhook_sender.deliver(
            url,
            "secret",
            {
                "type": "test.event",
                "id": "00000000-0000-4000-8000-000000000001",
                "timestamp": "2026-08-14T12:34:56+00:00",
            },
        )

        assert status == 0
        assert body == "Webhook delivery transport failed"
        assert url not in caplog.text
        assert "private-path" not in caplog.text
        assert "connection secret detail" not in caplog.text

    @pytest.mark.anyio
    @pytest.mark.parametrize("timestamp", [None, "", "not-a-date", "2026-08-14T12:34:56"])
    async def test_delivery_rejects_missing_invalid_or_naive_snapshot_timestamp(self, monkeypatch, timestamp):
        async def must_not_resolve(_hostname: str):
            raise AssertionError("invalid immutable envelope must be rejected before DNS")

        monkeypatch.setattr(webhook_sender, "resolve_public_webhook_addresses", must_not_resolve)
        envelope = {
            "type": "test.event",
            "id": "00000000-0000-4000-8000-000000000001",
            "timestamp": timestamp,
        }

        status, body = await webhook_sender.deliver("https://hooks.example.com/events", "secret", envelope)

        assert status == 0
        assert body == "Webhook delivery rejected"

    def test_should_retry_5xx(self):
        assert should_retry(500) is True
        assert should_retry(502) is True
        assert should_retry(503) is True

    def test_should_retry_429(self):
        assert should_retry(429) is True

    def test_should_retry_timeout(self):
        assert should_retry(0) is True

    def test_should_not_retry_4xx(self):
        assert should_retry(400) is False
        assert should_retry(401) is False
        assert should_retry(404) is False

    def test_should_not_retry_2xx(self):
        assert should_retry(200) is False
        assert should_retry(201) is False
