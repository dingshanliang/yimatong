"""通用 HTTP 连接器适配器。

适用于自建商城等提供标准 REST API 的外部平台。
connector.config 需包含:
  - api_url: 外部 API 基地址（必须 HTTPS，禁止内网地址）
  - api_key: Bearer token（敏感，应存 secrets_encrypted）
connector.secrets_encrypted 需包含:
  - callback_secret: HMAC-SHA256 回调签名密钥（可选）
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
from urllib.parse import urlparse

import httpx

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter
from app.services.connectors.secrets import decrypt_secrets

logger = logging.getLogger(__name__)


def _is_url_safe(url: str) -> bool:
    """验证 URL 是否为 HTTPS 且指向公网地址，防止 SSRF。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # 是域名而非 IP，允许
        return True
    except Exception:
        return False


class GenericHttpAdapter(BaseConnectorAdapter):
    async def sync_stock(self, connector: Connector) -> int:
        api_url = connector.config.get("api_url", "")
        api_key = connector.config.get("api_key", "")
        if not _is_url_safe(api_url):
            raise ValueError("api_url must be a valid HTTPS URL to a public domain")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/stock",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("available", data.get("count", 0))

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        api_url = connector.config.get("api_url", "")
        api_key = connector.config.get("api_key", "")
        benefit_type = benefit_config.get("benefit_type", "coupon")
        if not _is_url_safe(api_url):
            raise ValueError("api_url must be a valid HTTPS URL to a public domain")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{api_url}/deliver",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "consumer_id": consumer_id,
                    "benefit_type": benefit_type,
                    "benefit_config": benefit_config,
                },
            )
            resp.raise_for_status()
            ext_data = resp.json()

        return DeliveryResult(
            status=ext_data.get("status", "success"),
            external_id=ext_data.get("id") or ext_data.get("code"),
            external_data=ext_data,
            message=ext_data.get("message", ""),
        )

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        try:
            data = json.loads(request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return CallbackResult(status="failed")

        return CallbackResult(
            external_id=data.get("id") or data.get("delivery_id"),
            status=data.get("status", "pending"),
            external_data=data,
        )

    async def verify_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> bool:
        """HMAC-SHA256 回调签名验证。需要 secrets 中配置 callback_secret。"""
        if not connector.secrets_encrypted:
            return False
        secrets = decrypt_secrets(connector.secrets_encrypted)
        callback_secret = secrets.get("callback_secret")
        if not callback_secret:
            return False

        received_sig = headers.get("x-callback-sig", headers.get("X-Callback-Sig", ""))
        expected_sig = hmac.new(
            callback_secret.encode(),
            request_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(received_sig, expected_sig)

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        api_url = config.get("api_url")
        if not api_url:
            return False, "api_url is required"
        if not _is_url_safe(api_url):
            return False, "api_url must be a valid HTTPS URL to a public domain"
        return True, ""


register_adapter("generic_http", GenericHttpAdapter)
