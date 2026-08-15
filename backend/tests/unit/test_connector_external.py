"""外部权益连接器单元测试 — 适配器注册表架构

测试范围:
- 适配器注册表 (registry)
- GenericHttpAdapter (SSRF 防护、配置验证)
- CouponPoolAdapter (配置验证)
- BenefitDelivery 模型
- 凭证加密/解密/脱敏
"""

import json
import os
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.connectors.coupon_pool  # noqa: F401

# 触发适配器自注册
import app.services.connectors.generic_http  # noqa: F401
from app.models.connector import Connector
from app.services.circuit_breaker import CircuitBreaker
from app.services.connectors import get_adapter
from app.services.connectors.registry import list_adapter_types
from app.services.connectors.secrets import (
    connector_with_runtime_secrets,
    decrypt_secrets,
    encrypt_secrets,
    mask_secrets,
)


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
        config={
            "api_url": "https://api.example.com",
            "api_key": "sk-test",
            "provider_idempotency": True,
            "reconciliation_path": "deliveries/{idempotency_key}",
        },
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
            id=uuid7(),
            tenant_id=tenant_id,
            name="x",
            connector_type="nonexistent",
            config={},
            enabled=True,
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
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="generic_http",
                config={},
                enabled=True,
            )
        )
        is_valid, error = await adapter.validate_config({})
        assert not is_valid
        assert "api_url" in error

    @pytest.mark.asyncio
    async def test_validate_config_requires_provider_idempotency_and_reconciliation(self):
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="generic_http",
                config={},
                enabled=True,
            )
        )
        is_valid, error = await adapter.validate_config({"api_url": "https://api.example.com"})
        assert not is_valid
        assert "provider_idempotency" in error

        is_valid, error = await adapter.validate_config(
            {"api_url": "https://api.example.com", "provider_idempotency": True}
        )
        assert not is_valid
        assert "reconciliation_path" in error

    @pytest.mark.parametrize(
        "path",
        [
            "/deliveries/{idempotency_key}",
            "../deliveries/{idempotency_key}",
            "deliveries/{other}",
            "https://other.example/{idempotency_key}",
            "deliveries/{idempotency_key}?secret=x",
        ],
    )
    def test_reconciliation_path_is_bounded_to_provider_base(self, path):
        from app.services.connectors.generic_http import _is_reconciliation_path_valid

        assert _is_reconciliation_path_valid(path) is False

    @pytest.mark.asyncio
    async def test_ambiguous_delivery_is_quarantined_then_reconciled_without_second_post(
        self, generic_connector, monkeypatch
    ):
        from app.services.connectors import generic_http

        calls = []

        async def request(method, _base, suffix, **kwargs):
            calls.append((method, suffix, kwargs))
            if method == "POST":
                raise generic_http.AmbiguousDeliveryResult
            return {"status": "success", "id": "provider-delivery-1"}

        monkeypatch.setattr(generic_http, "_request_json", request)
        adapter = get_adapter(generic_connector)
        pending = await adapter.deliver(
            generic_connector,
            "consumer-1",
            {"benefit_type": "coupon", "idempotency_key": "stable-provider-key"},
        )
        assert pending.status == "pending"
        assert pending.external_id == "stable-provider-key"
        assert pending.external_data == {"status": "pending", "reason": "ambiguous_provider_outcome"}

        reconciled = await adapter.reconcile(generic_connector, pending.external_id)
        repeated_reconciliation = await adapter.reconcile(generic_connector, pending.external_id)
        assert reconciled.status == "success"
        assert repeated_reconciliation.status == "success"
        assert [method for method, _, _ in calls] == ["POST", "GET", "GET"]
        assert calls[1][1] == "deliveries/stable-provider-key"

    @pytest.mark.asyncio
    async def test_validate_config_rejects_http(self):
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="generic_http",
                config={},
                enabled=True,
            )
        )
        is_valid, error = await adapter.validate_config({"api_url": "http://api.example.com"})
        assert not is_valid
        assert "HTTPS" in error

    @pytest.mark.asyncio
    async def test_validate_config_rejects_localhost(self):
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="generic_http",
                config={},
                enabled=True,
            )
        )
        is_valid, error = await adapter.validate_config({"api_url": "https://localhost"})
        assert not is_valid

    @pytest.mark.asyncio
    async def test_validate_config_rejects_private_ip(self):
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="generic_http",
                config={},
                enabled=True,
            )
        )
        is_valid, error = await adapter.validate_config({"api_url": "https://192.168.1.1"})
        assert not is_valid

    @pytest.mark.asyncio
    async def test_verify_callback_default_deny(self, generic_connector):
        adapter = get_adapter(generic_connector)
        result = await adapter.verify_callback(generic_connector, b"{}", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_callback_with_hmac(self):
        import hashlib
        import hmac

        secret = "my-callback-secret"
        body = b'{"status":"success"}'
        expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        conn = Connector(
            id=uuid7(),
            tenant_id=uuid7(),
            name="x",
            connector_type="generic_http",
            config={"api_url": "https://api.example.com"},
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

    @pytest.mark.asyncio
    async def test_rejects_hostname_when_any_dns_answer_is_not_public(self, monkeypatch):
        from app.services.connectors import generic_http

        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]
        )
        monkeypatch.setattr(generic_http.asyncio, "get_running_loop", lambda: loop)

        with pytest.raises(ValueError, match="public addresses"):
            await generic_http._resolve_public_addresses("rebind.example")

    @pytest.mark.asyncio
    async def test_request_connects_to_the_prevalidated_address_with_tls_hostname(self, monkeypatch):
        from app.services.connectors import generic_http

        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        monkeypatch.setattr(generic_http.asyncio, "get_running_loop", lambda: loop)
        response = b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{"available":7}'
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[response, b""])
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        open_connection = AsyncMock(return_value=(reader, writer))
        monkeypatch.setattr(generic_http.asyncio, "open_connection", open_connection)

        result = await generic_http._request_json(
            "GET",
            "https://api.example.com/v1",
            "stock",
            headers={"Authorization": "Bearer secret"},
        )

        assert result == {"available": 7}
        assert open_connection.await_args.kwargs["host"] == "93.184.216.34"
        assert open_connection.await_args.kwargs["server_hostname"] == "api.example.com"
        raw_request = b"".join(call.args[0] for call in writer.write.call_args_list)
        assert b"Host: api.example.com" in raw_request
        assert b"GET /v1/stock HTTP/1.1" in raw_request

    @pytest.mark.asyncio
    async def test_complete_json_response_survives_connection_reset_during_close(self, monkeypatch):
        from app.services.connectors import generic_http

        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        monkeypatch.setattr(generic_http.asyncio, "get_running_loop", lambda: loop)
        response = b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\nConnection: close\r\n\r\n{"status":"success"}'
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[response])
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock(side_effect=ConnectionResetError("peer reset on close"))
        monkeypatch.setattr(generic_http.asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))

        result = await generic_http._request_json(
            "POST",
            "https://api.example.com",
            "deliver",
            headers={"Idempotency-Key": "stable-key"},
            payload={"value": 1},
            allow_address_failover=False,
        )

        assert result == {"status": "success"}
        assert writer.write.call_count >= 2

    @pytest.mark.asyncio
    async def test_incomplete_response_after_post_is_ambiguous_and_never_address_failed_over(self, monkeypatch):
        from app.services.connectors import generic_http

        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443)),
            ]
        )
        monkeypatch.setattr(generic_http.asyncio, "get_running_loop", lambda: loop)
        incomplete = b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\nConnection: close\r\n\r\n{"status":'
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[incomplete, b""])
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock(side_effect=ConnectionResetError("peer reset"))
        open_connection = AsyncMock(return_value=(reader, writer))
        monkeypatch.setattr(generic_http.asyncio, "open_connection", open_connection)

        with pytest.raises(generic_http.AmbiguousDeliveryResult):
            await generic_http._request_json(
                "POST",
                "https://api.example.com",
                "deliver",
                headers={"Idempotency-Key": "stable-key"},
                payload={"value": 1},
            )

        assert open_connection.await_count == 1

    @pytest.mark.asyncio
    async def test_full_invalid_json_is_not_misclassified_as_transport_ambiguity(self, monkeypatch):
        from app.services.connectors import generic_http

        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        monkeypatch.setattr(generic_http.asyncio, "get_running_loop", lambda: loop)
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\nnot-json"
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[response])
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        monkeypatch.setattr(generic_http.asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))

        with pytest.raises(json.JSONDecodeError):
            await generic_http._request_json(
                "POST",
                "https://api.example.com",
                "deliver",
                headers={"Idempotency-Key": "stable-key"},
                payload={"value": 1},
                allow_address_failover=False,
            )


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
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="coupon_pool",
                config={},
                enabled=True,
            )
        )
        is_valid, error = await adapter.validate_config({})
        assert not is_valid
        assert "pool_id" in error

    @pytest.mark.asyncio
    async def test_validate_config_invalid_uuid(self):
        adapter = get_adapter(
            Connector(
                id=uuid7(),
                tenant_id=uuid7(),
                name="x",
                connector_type="coupon_pool",
                config={},
                enabled=True,
            )
        )
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

    def test_runtime_secret_view_never_dirties_persisted_config(self, tenant_id):
        connector = Connector(
            id=uuid7(),
            tenant_id=tenant_id,
            name="safe-runtime-view",
            connector_type="generic_http",
            config={"api_url": "https://api.example.com"},
            secrets_encrypted=encrypt_secrets({"api_key": "runtime-only"}),
            enabled=True,
        )

        runtime = connector_with_runtime_secrets(connector)

        assert runtime.config["api_key"] == "runtime-only"
        assert connector.config == {"api_url": "https://api.example.com"}


# ---------------------------------------------------------------------------
# 6. BenefitDelivery 模型
# ---------------------------------------------------------------------------


class TestBenefitDeliveryModel:
    """模型字段验证"""

    def test_model_fields(self):
        from app.models.connector import BenefitDelivery

        expected = [
            "id",
            "tenant_id",
            "connector_id",
            "consumer_id",
            "benefit_type",
            "benefit_config",
            "status",
            "retry_count",
            "max_retries",
            "external_data",
            "next_retry_at",
            "created_at",
            "updated_at",
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
