"""通用 HTTP 连接器适配器。

适用于自建商城等提供标准 REST API 的外部平台。
connector.config 需包含:
  - api_url: 外部 API 基地址（必须 HTTPS，禁止内网地址）
  - api_key: Bearer token（敏感，应存 secrets_encrypted）
connector.secrets_encrypted 需包含:
  - callback_secret: HMAC-SHA256 回调签名密钥（可选）
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import ssl
import string
from urllib.parse import urlparse

import h11

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter
from app.services.connectors.secrets import decrypt_secrets

logger = logging.getLogger(__name__)


_MAX_RESPONSE_BYTES = 1_048_576
_MAX_RESOLVED_ADDRESSES = 8
_RECONCILIATION_FIELDS = {"idempotency_key"}


class AmbiguousDeliveryResult(Exception):
    """The provider may have accepted a delivery whose response was not observed."""


def _parse_safe_base_url(url: str):
    """Validate the stable URL shape before any DNS or network activity."""

    try:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("api_url must be a credential-free HTTPS URL")
        hostname = parsed.hostname
        if not hostname or parsed.port not in {None, 443}:
            raise ValueError("api_url must use a hostname on HTTPS port 443")
        if hostname.lower().rstrip(".") in {"localhost", "localhost.localdomain"}:
            raise ValueError("api_url must use a public hostname")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return parsed
        raise ValueError("api_url must use a public hostname, not an IP literal")
    except (TypeError, ValueError):
        raise ValueError("api_url must be a credential-free HTTPS URL") from None


def _is_url_safe(url: str) -> bool:
    try:
        _parse_safe_base_url(url)
        return True
    except ValueError:
        return False


async def _resolve_public_addresses(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("api_url hostname could not be resolved") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses or len(addresses) > _MAX_RESOLVED_ADDRESSES:
        raise ValueError("api_url hostname has an invalid address set")
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("api_url hostname must resolve only to public addresses")
    return addresses


async def _request_json(
    method: str,
    base_url: str,
    suffix: str,
    *,
    headers: dict[str, str],
    payload: dict | None = None,
    allow_address_failover: bool = True,
) -> dict:
    """Send one no-redirect HTTPS request over an address validated and pinned for this call."""

    parsed = _parse_safe_base_url(base_url)
    assert parsed.hostname is not None
    addresses = await _resolve_public_addresses(parsed.hostname)
    base_path = parsed.path.rstrip("/")
    target = f"{base_path}/{suffix.lstrip('/')}" or "/"
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else b""
    request_headers = {
        "Host": parsed.hostname,
        "User-Agent": "Yimatong-Connector/1.0",
        "Accept": "application/json",
        "Connection": "close",
        **headers,
    }
    if body:
        request_headers["Content-Type"] = "application/json"
        request_headers["Content-Length"] = str(len(body))

    last_error: Exception | None = None
    for address in addresses:
        writer = None
        request_may_have_been_written = False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=address,
                    port=443,
                    ssl=ssl.create_default_context(),
                    server_hostname=parsed.hostname,
                ),
                timeout=5.0,
            )
            connection = h11.Connection(h11.CLIENT)
            request_may_have_been_written = True
            writer.write(
                connection.send(
                    h11.Request(
                        method=method.encode("ascii"),
                        target=target.encode("ascii"),
                        headers=[
                            (key.encode("ascii"), value.encode("utf-8")) for key, value in request_headers.items()
                        ],
                    )
                )
            )
            if body:
                writer.write(connection.send(h11.Data(data=body)))
            writer.write(connection.send(h11.EndOfMessage()))
            await writer.drain()

            status_code: int | None = None
            response_body = bytearray()
            while True:
                event = connection.next_event()
                if event is h11.NEED_DATA:
                    chunk = await asyncio.wait_for(reader.read(64 * 1024), timeout=10.0)
                    if not chunk:
                        connection.receive_data(b"")
                    else:
                        connection.receive_data(chunk)
                    continue
                if isinstance(event, h11.Response):
                    status_code = event.status_code
                elif isinstance(event, h11.Data):
                    response_body.extend(event.data)
                    if len(response_body) > _MAX_RESPONSE_BYTES:
                        raise ValueError("connector response is too large")
                elif isinstance(event, h11.EndOfMessage):
                    break
                elif isinstance(event, h11.ConnectionClosed):
                    raise AmbiguousDeliveryResult("connector response ended before completion")
            if status_code is None or not 200 <= status_code < 300:
                raise ValueError(f"connector returned HTTP {status_code or 502}")
            result = json.loads(response_body or b"{}")
            if not isinstance(result, dict):
                raise ValueError("connector response must be a JSON object")
            return result
        except (OSError, TimeoutError, ssl.SSLError, h11.RemoteProtocolError) as exc:
            last_error = exc
            if request_may_have_been_written:
                raise AmbiguousDeliveryResult("connector delivery outcome is unknown") from exc
            if not allow_address_failover:
                raise
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, TimeoutError, ssl.SSLError):
                    # Closing is best-effort. It must not override a complete,
                    # already parsed provider response or reclassify it for retry.
                    pass
    raise ValueError("connector HTTPS request failed") from last_error


class GenericHttpAdapter(BaseConnectorAdapter):
    async def sync_stock(self, connector: Connector) -> int:
        api_url = connector.config.get("api_url", "")
        api_key = connector.config.get("api_key", "")
        if not _is_url_safe(api_url):
            raise ValueError("api_url must be a valid HTTPS URL to a public domain")
        if connector.config.get("provider_idempotency") is not True:
            raise ValueError("generic_http delivery requires provider_idempotency=true")
        reconciliation_path = connector.config.get("reconciliation_path")
        if not _is_reconciliation_path_valid(reconciliation_path):
            raise ValueError("generic_http delivery requires a bounded reconciliation_path")
        data = await _request_json("GET", api_url, "stock", headers={"Authorization": f"Bearer {api_key}"})
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
        if connector.config.get("provider_idempotency") is not True:
            raise ValueError("generic_http delivery requires provider_idempotency=true")
        if not _is_reconciliation_path_valid(connector.config.get("reconciliation_path")):
            raise ValueError("generic_http delivery requires a bounded reconciliation_path")

        request_headers = {"Authorization": f"Bearer {api_key}"}
        idempotency_key = benefit_config.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("generic_http delivery requires a stable idempotency_key")
        request_headers["Idempotency-Key"] = idempotency_key
        request_payload = {
            "consumer_id": consumer_id,
            "benefit_type": benefit_type,
            "benefit_config": benefit_config,
        }
        request_payload["idempotency_key"] = idempotency_key
        try:
            ext_data = await _request_json(
                "POST",
                api_url,
                "deliver",
                headers=request_headers,
                payload=request_payload,
                allow_address_failover=False,
            )
        except AmbiguousDeliveryResult:
            return DeliveryResult(
                status="pending",
                external_id=idempotency_key,
                external_data={"status": "pending", "reason": "ambiguous_provider_outcome"},
                message="Provider outcome requires reconciliation",
            )

        provider_status = ext_data.get("status", "success")
        return DeliveryResult(
            status="success" if provider_status == "success" else "pending",
            external_id=ext_data.get("id") or ext_data.get("code"),
            external_data=(
                ext_data
                if provider_status == "success"
                else {**ext_data, "reason": "provider_outcome_requires_reconciliation"}
            ),
            message=ext_data.get("message", ""),
        )

    async def reconcile(self, connector: Connector, idempotency_key: str) -> DeliveryResult:
        """Query a provider outcome without repeating the value-bearing POST."""

        api_url = connector.config.get("api_url", "")
        api_key = connector.config.get("api_key", "")
        path = connector.config.get("reconciliation_path")
        if not _is_url_safe(api_url) or not _is_reconciliation_path_valid(path):
            raise ValueError("generic_http reconciliation is not configured")
        data = await _request_json(
            "GET",
            api_url,
            path.format(idempotency_key=idempotency_key),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        return DeliveryResult(
            status=data.get("status", "pending"),
            external_id=data.get("id") or data.get("code") or idempotency_key,
            external_data=data,
            message=data.get("message", ""),
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
        if config.get("provider_idempotency") is not True:
            return False, "provider_idempotency=true is required"
        if not _is_reconciliation_path_valid(config.get("reconciliation_path")):
            return False, "reconciliation_path must be a bounded relative path containing {idempotency_key}"
        return True, ""


def _is_reconciliation_path_valid(path: object) -> bool:
    if not isinstance(path, str) or not 1 <= len(path) <= 256 or path.startswith(("/", "http:", "https:")):
        return False
    if ".." in path or "?" in path or "#" in path or "\\" in path:
        return False
    try:
        fields = {field for _, field, _, _ in string.Formatter().parse(path) if field}
    except ValueError:
        return False
    return fields == _RECONCILIATION_FIELDS


register_adapter("generic_http", GenericHttpAdapter)
