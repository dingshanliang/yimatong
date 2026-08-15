"""A6-007: scan_token 防伪机制测试"""

import time
import uuid
from datetime import UTC, datetime

import pytest

from app.services.scan_token import (
    bind_scan_token_consumer,
    create_scan_token,
    require_launch_claim_authority,
    verify_scan_token,
)


class TestScanToken:
    def test_create_and_verify(self):
        token = create_scan_token(
            public_id="ABC123",
            ip_hash="abc123hash",
        )
        assert token is not None
        result = verify_scan_token(token, "ABC123")
        assert result is not None
        assert result["public_id"] == "ABC123"
        assert result["ip_hash"] == "abc123hash"

    def test_wrong_public_id_fails(self):
        token = create_scan_token(public_id="ABC123", ip_hash="hash")
        result = verify_scan_token(token, "DIFFERENT")
        assert result is None

    def test_invalid_token_fails(self):
        result = verify_scan_token("invalid.token.here", "ABC123")
        assert result is None

    def test_token_contains_expiry(self):
        token = create_scan_token(public_id="ABC123", ip_hash="hash")
        result = verify_scan_token(token, "ABC123")
        assert "exp" in result
        # 默认 5 分钟有效期
        assert result["exp"] > time.time()

    def test_tampered_token_fails(self):
        token = create_scan_token(public_id="ABC123", ip_hash="hash")
        # 篡改 token
        tampered = token[:-5] + "XXXXX"
        result = verify_scan_token(tampered, "ABC123")
        assert result is None

    def test_create_with_tenant_id(self):
        token = create_scan_token(
            public_id="ABC123",
            ip_hash="abc123hash",
            tenant_id="tenant-001",
        )
        result = verify_scan_token(token, "ABC123")
        assert result is not None
        assert result["tenant_id"] == "tenant-001"

    def test_token_binds_authoritative_scan_event(self):
        scan_event_id = uuid.uuid4()
        scan_time = datetime(2026, 8, 12, 2, 30, tzinfo=UTC)
        token = create_scan_token(
            public_id="ABC123",
            ip_hash="abc123hash",
            tenant_id="tenant-001",
            scan_event_id=str(scan_event_id),
            scan_time=scan_time.isoformat(),
        )

        result = verify_scan_token(token, "ABC123")

        assert result is not None
        assert result["scan_event_id"] == str(scan_event_id)
        assert result["scan_time"] == "2026-08-12T02:30:00+00:00"

    def test_v2_token_binds_the_exact_brand_confirmed_live_release(self):
        release_id = uuid.uuid4()
        campaign_id = uuid.uuid4()
        code_batch_id = uuid.uuid4()
        digest = "a" * 64

        payload = verify_scan_token(
            create_scan_token(
                public_id="ABC123",
                ip_hash="abc123hash",
                tenant_id=str(uuid.uuid4()),
                scan_event_id=str(uuid.uuid4()),
                visitor_id=str(uuid.uuid4()),
                launch_release_id=str(release_id),
                campaign_id=str(campaign_id),
                code_batch_id=str(code_batch_id),
                content_digest=digest,
            )
        )

        authority = require_launch_claim_authority(payload)
        assert payload["version"] == 2
        assert authority.launch_release_id == release_id
        assert authority.campaign_id == campaign_id
        assert authority.code_batch_id == code_batch_id
        assert authority.content_digest == digest

    def test_claim_authority_rejects_a_legacy_or_partially_bound_token(self):
        legacy = verify_scan_token(
            create_scan_token(
                public_id="ABC123",
                ip_hash="abc123hash",
                tenant_id=str(uuid.uuid4()),
                scan_event_id=str(uuid.uuid4()),
                visitor_id=str(uuid.uuid4()),
            )
        )
        with pytest.raises(ValueError, match="launch authority"):
            require_launch_claim_authority(legacy)

        with pytest.raises(ValueError, match="all launch authority fields"):
            create_scan_token(
                public_id="ABC123",
                ip_hash="abc123hash",
                tenant_id=str(uuid.uuid4()),
                launch_release_id=str(uuid.uuid4()),
            )

    def test_consumer_binding_preserves_claim_authority(self):
        consumer_id = uuid.uuid4()
        scan_event_id = uuid.uuid4()
        visitor_id = uuid.uuid4()
        release_id = uuid.uuid4()
        campaign_id = uuid.uuid4()
        code_batch_id = uuid.uuid4()
        digest = "b" * 64
        scan_time = "2026-08-12T02:30:00+00:00"
        original = verify_scan_token(
            create_scan_token(
                public_id="ABC123",
                ip_hash="old-ip",
                tenant_id=str(uuid.uuid4()),
                scan_event_id=str(scan_event_id),
                scan_time=scan_time,
                visitor_id=str(visitor_id),
                launch_release_id=str(release_id),
                campaign_id=str(campaign_id),
                code_batch_id=str(code_batch_id),
                content_digest=digest,
            )
        )

        rebound = verify_scan_token(bind_scan_token_consumer(original, consumer_id, "new-ip"))

        assert rebound is not None
        assert rebound["consumer_id"] == str(consumer_id)
        assert rebound["scan_event_id"] == str(scan_event_id)
        assert rebound["scan_time"] == scan_time
        assert rebound["visitor_id"] == str(visitor_id)
        assert rebound["public_id"] == original["public_id"]
        assert rebound["tenant_id"] == original["tenant_id"]
        assert rebound["ip_hash"] == "new-ip"
        assert rebound["version"] == 2
        assert rebound["launch_release_id"] == str(release_id)
        assert rebound["campaign_id"] == str(campaign_id)
        assert rebound["code_batch_id"] == str(code_batch_id)
        assert rebound["content_digest"] == digest

    def test_consumer_binding_rejects_token_without_claim_authority(self):
        original = verify_scan_token(create_scan_token("ABC123", "ip", tenant_id=str(uuid.uuid4())))

        with pytest.raises(ValueError, match="scan token missing consumer binding authority"):
            bind_scan_token_consumer(original, uuid.uuid4(), "ip")

    def test_verify_with_expected_tenant_id(self):
        token = create_scan_token(
            public_id="ABC123",
            ip_hash="abc123hash",
            tenant_id="tenant-001",
        )
        # 匹配
        result = verify_scan_token(token, "ABC123", expected_tenant_id="tenant-001")
        assert result is not None
        # 不匹配
        result = verify_scan_token(token, "ABC123", expected_tenant_id="tenant-999")
        assert result is None

    def test_default_ttl_is_1800(self):
        token = create_scan_token(public_id="ABC123", ip_hash="hash")
        result = verify_scan_token(token, "ABC123")
        assert result["exp"] - int(time.time()) <= 1800
        assert result["exp"] - int(time.time()) > 1700

    def test_ip_hash_none_is_valid(self):
        """ip_hash=None 应被正常存入 token"""
        token = create_scan_token(public_id="ABC123", ip_hash=None)
        result = verify_scan_token(token, "ABC123")
        assert result is not None
        assert result["ip_hash"] is None

    def test_ip_hash_verification_with_none_skips(self):
        """expected_ip_hash=None 时应跳过 IP 验证"""
        token = create_scan_token(public_id="ABC123", ip_hash="some_hash")
        result = verify_scan_token(token, "ABC123", expected_ip_hash=None)
        assert result is not None
