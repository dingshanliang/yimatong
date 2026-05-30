"""外部权益连接器单元测试 — 适配器注册表架构

测试范围:
- 适配器注册表 (registry)
- GenericHttpAdapter (SSRF 防护、配置验证)
- CouponPoolAdapter (配置验证)
- BenefitDelivery 模型
- 凭证加密/解密/脱敏
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.connector import Connector
from app.services.circuit_breaker import CircuitBreaker
from app.services.connectors import get_adapter, register_adapter
from app.services.connectors.base import BaseConnectorAdapter, DeliveryResult
from app.services.connectors.registry import list_adapter_types
from app.services.connectors.secrets import (
    decrypt_secrets,
    encrypt_secrets,
    mask_secrets,
)

# 触发适配器自注册
import app.services.connectors.generic_http  # noqa: F401
import app.services.connectors.coupon_pool  # noqa: F401


def uuid7():
    from uuid6 import uuid7
    return uuid7()


@pytest.fixture
def tenant_id():
    return uuid7()


@pytest.fixture
def generic_connector(tenant_id):
    return Connector(
        id=uuid7(),
        tenant_id=tenant_id,
        name="测试 HTTP 连接器",
        connector_type="generic_http",
        config={"api_url": "https://api.example.com", "api_key": "sk-test"},
        enabled=True,
    )


@pytest.fixture
def pool_connector(tenant_id):
    pool_id = uuid7()
    return Connector(
        id=uuid7(),
        tenant_id=tenant_id,
        name="测试券码池连接器",
        connector_type="coupon_pool",
        config={"pool_id": str(pool_id)},
        enabled=True,
    )


# ---------------------------------------------------------------------------
# 1. 适配器注册表
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """适配器注册和查找"""

    def test_registered_types(self):
        types = list_adapter_types()
        assert "generic_http" in types
        assert "coupon_pool" in types

    def test_get_generic_http_adapter(self, generic_connector):
        adapter = get_adapter(generic_connector)
        assert adapter is not None

    def test_get_coupon_pool_adapter(self, pool_connector):
        adapter = get_adapter(pool_connector)
        assert adapter is not None

    def test_unknown_type_raises(self, tenant_id):
        unknown = Connector(
            id=uuid7(), tenant_id=tenant_id, name="x",
            connector_type="nonexistent", config={}, enabled=True,
        )
        with pytest.raises(ValueError, match="Unknown connector type"):
            get_adapter(unknown)


# ---------------------------------------------------------------------------
# 2. GenericHttpAdapter
# ---------------------------------------------------------------------------


class TestGenericHttpAdapter:
    """通用 HTTP 适配器"""

    @pytest.mark.asyncio
    async def test_validate_config_ok(self, generic_connector):
        adapter = get_adapter(generic_connector)
        is_valid, error = await adapter.validate_config(generic_connector.config)
        assert is_valid
        assert error == ""

    @pytest.mark.asyncio
    async def test_validate_config_missing_url(self):
        adapter = get_adapter(Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="generic_http", config={}, enabled=True,
        ))
        is_valid, error = await adapter.validate_config({})
        assert not is_valid
        assert "api_url" in error

    @pytest.mark.asyncio
    async def test_validate_config_rejects_http(self):
        adapter = get_adapter(Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="generic_http", config={}, enabled=True,
        ))
        is_valid, error = await adapter.validate_config({"api_url": "http://api.example.com"})
        assert not is_valid
        assert "HTTPS" in error

    @pytest.mark.asyncio
    async def test_validate_config_rejects_localhost(self):
        adapter = get_adapter(Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="generic_http", config={}, enabled=True,
        ))
        is_valid, error = await adapter.validate_config({"api_url": "https://localhost"})
        assert not is_valid

    @pytest.mark.asyncio
    async def test_validate_config_rejects_private_ip(self):
        adapter = get_adapter(Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="generic_http", config={}, enabled=True,
        ))
        is_valid, error = await adapter.validate_config({"api_url": "https://192.168.1.1"})
        assert not is_valid

    @pytest.mark.asyncio
    async def test_verify_callback_default_deny(self, generic_connector):
        adapter = get_adapter(generic_connector)
        result = await adapter.verify_callback(generic_connector, b'{}', {})
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_callback_with_hmac(self):
        import hashlib
        import hmac

        secret = "my-callback-secret"
        body = b'{"status":"success"}'
        expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        conn = Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="generic_http", config={"api_url": "https://api.example.com"},
            secrets_encrypted=encrypt_secrets({"callback_secret": secret}),
            enabled=True,
        )
        adapter = get_adapter(conn)

        # 正确签名
        result = await adapter.verify_callback(conn, body, {"X-Callback-Sig": expected_sig})
        assert result is True

        # 错误签名
        result = await adapter.verify_callback(conn, body, {"X-Callback-Sig": "wrong"})
        assert result is False


# ---------------------------------------------------------------------------
# 3. SSRF 防护
# ---------------------------------------------------------------------------


class TestSSRFProtection:
    """URL 安全校验"""

    def test_allows_https_public(self):
        from app.services.connectors.generic_http import _is_url_safe
        assert _is_url_safe("https://api.example.com") is True

    def test_rejects_http(self):
        from app.services.connectors.generic_http import _is_url_safe
        assert _is_url_safe("http://api.example.com") is False

    def test_rejects_localhost(self):
        from app.services.connectors.generic_http import _is_url_safe
        assert _is_url_safe("https://localhost") is False
        assert _is_url_safe("https://127.0.0.1") is False

    def test_rejects_private_ip(self):
        from app.services.connectors.generic_http import _is_url_safe
        assert _is_url_safe("https://10.0.0.1") is False
        assert _is_url_safe("https://172.16.0.1") is False
        assert _is_url_safe("https://192.168.1.1") is False

    def test_rejects_empty(self):
        from app.services.connectors.generic_http import _is_url_safe
        assert _is_url_safe("") is False


# ---------------------------------------------------------------------------
# 4. CouponPoolAdapter
# ---------------------------------------------------------------------------


class TestCouponPoolAdapter:
    """券码池适配器"""

    @pytest.mark.asyncio
    async def test_validate_config_ok(self, pool_connector):
        adapter = get_adapter(pool_connector)
        is_valid, error = await adapter.validate_config(pool_connector.config)
        assert is_valid

    @pytest.mark.asyncio
    async def test_validate_config_missing_pool_id(self):
        adapter = get_adapter(Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="coupon_pool", config={}, enabled=True,
        ))
        is_valid, error = await adapter.validate_config({})
        assert not is_valid
        assert "pool_id" in error

    @pytest.mark.asyncio
    async def test_validate_config_invalid_uuid(self):
        adapter = get_adapter(Connector(
            id=uuid7(), tenant_id=uuid7(), name="x",
            connector_type="coupon_pool", config={}, enabled=True,
        ))
        is_valid, error = await adapter.validate_config({"pool_id": "not-a-uuid"})
        assert not is_valid


# ---------------------------------------------------------------------------
# 5. 凭证加密
# ---------------------------------------------------------------------------


class TestSecretsEncryption:
    """AES-GCM 凭证加密/解密/脱敏"""

    def setup_method(self):
        os.environ["AES_MASTER_KEY_V1"] = "a" * 64

    def test_encrypt_decrypt_roundtrip(self):
        secrets = {"api_key": "sk_live_12345678", "api_secret": "secret123"}
        encrypted = encrypt_secrets(secrets)
        decrypted = decrypt_secrets(encrypted)
        assert decrypted == secrets

    def test_encrypt_empty_returns_empty(self):
        assert encrypt_secrets({}) == b""
        assert decrypt_secrets(b"") == {}

    def test_mask_secrets(self):
        secrets = {"api_key": "sk_live_12345678", "short": "abc"}
        masked = mask_secrets(secrets)
        assert "sk_" in masked["api_key"]
        assert "678" in masked["api_key"]
        assert "***" in masked["api_key"]
        assert masked["short"] == "***"


# ---------------------------------------------------------------------------
# 6. BenefitDelivery 模型
# ---------------------------------------------------------------------------


class TestBenefitDeliveryModel:
    """模型字段验证"""

    def test_model_fields(self):
        from app.models.connector import BenefitDelivery
        expected = [
            "id", "tenant_id", "connector_id", "consumer_id",
            "benefit_type", "benefit_config", "status",
            "retry_count", "max_retries", "external_data",
            "next_retry_at", "created_at", "updated_at",
        ]
        for field in expected:
            assert hasattr(BenefitDelivery, field), f"Missing field: {field}"


# ---------------------------------------------------------------------------
# 7. Connector 模型新增字段
# ---------------------------------------------------------------------------


class TestConnectorModel:
    """Connector 模型字段验证"""

    def test_secrets_encrypted_field(self):
        assert hasattr(Connector, "secrets_encrypted")

    def test_created_updated_at_fields(self):
        assert hasattr(Connector, "created_at")
        assert hasattr(Connector, "updated_at")


# ---------------------------------------------------------------------------
# 8. 熔断器
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """熔断器集成"""

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(threshold=3, reset_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_available()  # 还没到阈值
        cb.record_failure()
        assert not cb.is_available()  # 熔断

    def test_resets_after_timeout(self):
        import time
        cb = CircuitBreaker(threshold=1, reset_timeout=1)
        cb.record_failure()
        assert not cb.is_available()
        time.sleep(1.1)
        assert cb.is_available()  # 超时恢复

    def test_success_resets_failures(self):
        cb = CircuitBreaker(threshold=3, reset_timeout=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._state.failure_count == 0
