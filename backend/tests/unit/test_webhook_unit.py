"""EPIC-19 单元测试：事件总线、权限映射、签名生成。"""

import hashlib
import hmac
import json

import pytest

from app.core.event_bus import _EventBus
from app.core.permissions import (
    VALID_ROLES,
    get_permissions_for_role,
    role_has_permission,
    ROLE_PERMISSIONS,
)
from app.services.webhook_sender import build_envelope, compute_signature, should_retry


class TestPermissions:
    """角色权限映射测试。"""

    def test_all_roles_have_permissions(self):
        for role in VALID_ROLES:
            perms = get_permissions_for_role(role)
            assert len(perms) > 0, f"Role {role} has no permissions"

    def test_data_reader_cannot_write(self):
        assert not role_has_permission("data_reader", "coupon:issue")
        assert not role_has_permission("data_reader", "campaign:status")

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
