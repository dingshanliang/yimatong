"""A6-007: scan_token 防伪机制测试"""

import time

from app.services.scan_token import create_scan_token, verify_scan_token


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
