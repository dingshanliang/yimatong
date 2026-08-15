"""Security-hardened webhook HTTP sender.

The sender resolves and pins a public address for every attempt. It never
follows redirects and signs the exact canonical bytes written to the socket.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import socket
import ssl
import uuid
from datetime import UTC, datetime
from urllib.parse import SplitResult, urlsplit

import h11

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 10.0
MAX_WEBHOOK_URL_LENGTH = 500
MAX_WEBHOOK_BODY_BYTES = 1_048_576
MAX_WEBHOOK_RESPONSE_BYTES = 2_000
_MAX_RESOLVED_ADDRESSES = 8
_SAFE_EVENT_HEADER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class AmbiguousWebhookDelivery(Exception):
    """The request may have reached the endpoint but no complete response arrived."""


def canonical_json_bytes(value: dict) -> bytes:
    """Serialize webhook JSON with stable ordering and no insignificant spaces."""

    raw_body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        raise ValueError("Webhook payload exceeds the delivery size limit")
    return raw_body


def validate_webhook_url(url: str) -> SplitResult:
    """Validate URL shape without making a network request."""

    try:
        if not isinstance(url, str) or url != url.strip() or len(url) > MAX_WEBHOOK_URL_LENGTH:
            raise ValueError
        url.encode("ascii")
        if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in url):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
        ):
            raise ValueError
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".localhost", ".local")):
            raise ValueError
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError
        hostname.encode("idna").decode("ascii")
        return parsed
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Webhook URL must be a credential-free public HTTPS URL on port 443") from None


async def resolve_public_webhook_addresses(hostname: str) -> list[str]:
    """Reject the full DNS answer set if any address is non-public."""

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        addresses = sorted({str(info[4][0]) for info in infos})
    except OSError as exc:
        raise ValueError("Webhook URL hostname could not be resolved") from exc
    if not addresses or len(addresses) > _MAX_RESOLVED_ADDRESSES:
        raise ValueError("Webhook URL hostname has an invalid address set")
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Webhook URL hostname must resolve only to public addresses")
    return addresses


async def validate_webhook_destination(url: str) -> str:
    """Validate an endpoint at admission time and return its stable URL."""

    parsed = validate_webhook_url(url)
    assert parsed.hostname is not None
    await resolve_public_webhook_addresses(parsed.hostname)
    return url


def build_envelope(event_type: str, data: dict, tenant_id: str, event_id: str | None = None) -> dict:
    """Build the versioned delivery envelope."""

    return {
        "id": event_id or str(uuid.uuid4()),
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "data": data,
    }


def compute_signature(secret: str, raw_body: bytes, timestamp: str | None = None) -> str:
    """Compute HMAC-SHA256, binding timestamp and body for live delivery."""

    signed = raw_body if timestamp is None else timestamp.encode("ascii") + b"." + raw_body
    mac = hmac.new(secret.encode(), signed, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def envelope_signature_timestamp(envelope: dict) -> str:
    """Derive a stable epoch-second signature timestamp from the immutable envelope."""

    value = envelope.get("timestamp")
    if not isinstance(value, str) or not value:
        raise ValueError("Webhook envelope timestamp is required")
    try:
        occurred_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Webhook envelope timestamp is invalid") from None
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("Webhook envelope timestamp must include a timezone")
    return str(int(occurred_at.astimezone(UTC).timestamp()))


async def _post_pinned(
    parsed: SplitResult,
    addresses: list[str],
    *,
    raw_body: bytes,
    headers: dict[str, str],
) -> tuple[int, str]:
    """POST once over TLS pinned to a pre-validated address."""

    assert parsed.hostname is not None
    hostname = parsed.hostname.encode("idna").decode("ascii")
    target = parsed.path or "/"
    request_headers = {
        "Host": hostname,
        "User-Agent": "Yimatong-Webhook/1.0",
        "Content-Type": "application/json",
        "Content-Length": str(len(raw_body)),
        "Connection": "close",
        **headers,
    }
    last_error: Exception | None = None
    for address in addresses:
        writer = None
        request_may_have_been_written = False
        try:
            async with asyncio.timeout(WEBHOOK_TIMEOUT):
                reader, writer = await asyncio.open_connection(
                    host=address,
                    port=443,
                    ssl=ssl.create_default_context(),
                    server_hostname=hostname,
                )
                connection = h11.Connection(h11.CLIENT)
                request_bytes = connection.send(
                    h11.Request(
                        method=b"POST",
                        target=target.encode("ascii"),
                        headers=[
                            (key.encode("ascii"), value.encode("utf-8")) for key, value in request_headers.items()
                        ],
                    )
                )
                writer.write(request_bytes)
                request_may_have_been_written = True
                writer.write(connection.send(h11.Data(data=raw_body)))
                writer.write(connection.send(h11.EndOfMessage()))
                await writer.drain()

                status_code: int | None = None
                response_body = bytearray()
                while True:
                    event = connection.next_event()
                    if event is h11.NEED_DATA:
                        chunk = await reader.read(64 * 1024)
                        connection.receive_data(chunk)
                        continue
                    if isinstance(event, h11.Response):
                        status_code = event.status_code
                    elif isinstance(event, h11.Data):
                        remaining = MAX_WEBHOOK_RESPONSE_BYTES - len(response_body)
                        if remaining > 0:
                            response_body.extend(event.data[:remaining])
                    elif isinstance(event, h11.EndOfMessage):
                        if status_code is None:
                            raise h11.RemoteProtocolError("response status missing")
                        return status_code, response_body.decode("utf-8", errors="replace")
                    elif isinstance(event, h11.ConnectionClosed):
                        raise AmbiguousWebhookDelivery("response ended before completion")
        except (OSError, TimeoutError, ssl.SSLError, h11.ProtocolError) as exc:
            last_error = exc
            if request_may_have_been_written:
                raise AmbiguousWebhookDelivery("delivery outcome is unknown") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, TimeoutError, ssl.SSLError):
                    pass
    raise OSError("Webhook HTTPS connection failed") from last_error


async def deliver(url: str, secret: str, envelope: dict) -> tuple[int, str]:
    """Deliver one immutable envelope, returning a bounded operational result."""

    try:
        parsed = validate_webhook_url(url)
        assert parsed.hostname is not None
        raw_body = canonical_json_bytes(envelope)
        event_type = envelope["type"]
        delivery_id = envelope["id"]
        if not isinstance(event_type, str) or _SAFE_EVENT_HEADER.fullmatch(event_type) is None:
            raise ValueError("Invalid webhook event type")
        try:
            uuid.UUID(str(delivery_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid webhook delivery id") from None
        timestamp = envelope_signature_timestamp(envelope)
        addresses = await resolve_public_webhook_addresses(parsed.hostname)
        headers = {
            "X-Ymt-Signature": compute_signature(secret, raw_body, timestamp),
            "X-Ymt-Signature-Version": "v1",
            "X-Ymt-Timestamp": timestamp,
            "X-Ymt-Event": event_type,
            "X-Ymt-Delivery": str(delivery_id),
        }
        return await _post_pinned(parsed, addresses, raw_body=raw_body, headers=headers)
    except (KeyError, TypeError, ValueError):
        logger.warning("Webhook delivery rejected before network access")
        return 0, "Webhook delivery rejected"
    except AmbiguousWebhookDelivery:
        logger.warning("Webhook delivery outcome is unknown")
        return 0, "Webhook delivery outcome is unknown"
    except (OSError, TimeoutError, ssl.SSLError, h11.ProtocolError):
        logger.warning("Webhook delivery transport failed")
        return 0, "Webhook delivery transport failed"


def should_retry(status_code: int) -> bool:
    """Return whether the delivery state machine may schedule another attempt."""

    return status_code == 0 or status_code == 429 or status_code >= 500


def build_batch_envelope(event_type: str, items: list[dict], tenant_id: str) -> dict:
    """Build a batch envelope."""

    return {
        "id": str(uuid.uuid4()),
        "type": f"batch.{event_type}",
        "timestamp": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "data": {"items": items, "count": len(items)},
    }
