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
