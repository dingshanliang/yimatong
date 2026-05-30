"""Webhook HTTP 投递器。

负责构造信封 payload、计算 HMAC-SHA256 签名、发送 HTTP POST。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 10  # seconds


def build_envelope(event_type: str, data: dict, tenant_id: str, event_id: str | None = None) -> dict:
    """构造信封 payload。"""
    return {
        "id": event_id or str(uuid.uuid4()),
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "data": data,
    }


def compute_signature(secret: str, raw_body: bytes) -> str:
    """计算 HMAC-SHA256 签名。"""
    mac = hmac.new(secret.encode(), raw_body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def deliver(
    url: str,
    secret: str,
    envelope: dict,
) -> tuple[int, str]:
    """发送 webhook HTTP POST，返回 (status_code, response_body)。

    异常情况返回 (0, error_message)。
    """
    raw_body = json.dumps(envelope, ensure_ascii=False).encode()
    signature = compute_signature(secret, raw_body)

    headers = {
        "Content-Type": "application/json",
        "X-Ymt-Signature": signature,
        "X-Ymt-Timestamp": str(int(datetime.now(UTC).timestamp())),
        "X-Ymt-Event": envelope["type"],
        "X-Ymt-Delivery": envelope["id"],
    }

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
            resp = await client.post(url, content=raw_body, headers=headers)
            body = resp.text[:2000]  # 限制存储长度
            return resp.status_code, body
    except Exception as e:
        logger.warning("Webhook delivery failed to %s: %s", url, e)
        return 0, str(e)[:500]


def should_retry(status_code: int) -> bool:
    """判断 HTTP 状态码是否需要重试。"""
    if status_code == 0:
        return True  # 连接/超时错误
    if status_code == 429:
        return True  # 限流
    if status_code >= 500:
        return True  # 服务端错误
    return False


def build_batch_envelope(event_type: str, items: list[dict], tenant_id: str) -> dict:
    """构造批量投递信封。"""
    return {
        "id": str(uuid.uuid4()),
        "type": f"batch.{event_type}",
        "timestamp": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "data": {"items": items, "count": len(items)},
    }
