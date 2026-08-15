"""scan_token 防伪机制"""

import re
import time
import uuid
from dataclasses import dataclass

import jwt

from app.core.config import settings

_LAUNCH_AUTHORITY_FIELDS = ("launch_release_id", "campaign_id", "code_batch_id", "content_digest")


@dataclass(frozen=True)
class ScanLaunchAuthority:
    launch_release_id: uuid.UUID
    campaign_id: uuid.UUID
    code_batch_id: uuid.UUID
    content_digest: str


def create_scan_token(
    public_id: str,
    ip_hash: str | None,
    tenant_id: str = "",
    consumer_id: str = "",
    scan_event_id: str = "",
    scan_time: str = "",
    visitor_id: str = "",
    launch_release_id: str = "",
    campaign_id: str = "",
    code_batch_id: str = "",
    content_digest: str = "",
    expires_in: int = 1800,
) -> str:
    """颁发 scan_token（短期 JWT，默认 30 分钟）。

    Args:
        public_id: 码的公开标识
        ip_hash: 客户端 IP 的 SHA256 哈希，None 表示无法获取
        tenant_id: 租户 ID（减少消费端查询）
        consumer_id: 可选，绑定的消费者 ID（消费者身份验证后写入）
        expires_in: 有效期秒数，默认 1800（30 分钟）
    """
    payload = {
        "version": 1,
        "public_id": public_id,
        "ip_hash": ip_hash,
        "tenant_id": tenant_id,
        "consumer_id": consumer_id,
        "scan_event_id": scan_event_id,
        "scan_time": scan_time,
        "visitor_id": visitor_id,
        "exp": int(time.time()) + expires_in,
        "type": "scan_token",
        "jti": str(uuid.uuid4()),
    }
    launch_values = (launch_release_id, campaign_id, code_batch_id, content_digest)
    if any(launch_values):
        if not all(isinstance(value, str) and value.strip() for value in launch_values):
            raise ValueError("scan token requires all launch authority fields")
        authority = require_launch_claim_authority(
            {
                "version": 2,
                "launch_release_id": launch_release_id,
                "campaign_id": campaign_id,
                "code_batch_id": code_batch_id,
                "content_digest": content_digest,
            }
        )
        payload.update(
            {
                "version": 2,
                "launch_release_id": str(authority.launch_release_id),
                "campaign_id": str(authority.campaign_id),
                "code_batch_id": str(authority.code_batch_id),
                "content_digest": authority.content_digest,
            }
        )
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def bind_scan_token_consumer(payload: dict, consumer_id: uuid.UUID, ip_hash: str | None) -> str:
    """Reissue a scan credential for a verified member without losing claim authority."""

    required = ("public_id", "tenant_id", "scan_event_id", "visitor_id")
    if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in required):
        raise ValueError("scan token missing consumer binding authority")
    launch_authority = None
    if payload.get("version") == 2:
        launch_authority = require_launch_claim_authority(payload)
    return create_scan_token(
        public_id=payload["public_id"],
        ip_hash=ip_hash,
        tenant_id=payload["tenant_id"],
        consumer_id=str(consumer_id),
        scan_event_id=payload["scan_event_id"],
        scan_time=payload.get("scan_time", ""),
        visitor_id=payload["visitor_id"],
        launch_release_id=str(launch_authority.launch_release_id) if launch_authority else "",
        campaign_id=str(launch_authority.campaign_id) if launch_authority else "",
        code_batch_id=str(launch_authority.code_batch_id) if launch_authority else "",
        content_digest=launch_authority.content_digest if launch_authority else "",
    )


def require_launch_claim_authority(payload: dict) -> ScanLaunchAuthority:
    """Parse the immutable live-release facts required for benefit claims."""

    if payload.get("version") != 2:
        raise ValueError("scan token missing launch authority version")
    values = {field: payload.get(field) for field in _LAUNCH_AUTHORITY_FIELDS}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("scan token missing launch authority fields")
    try:
        release_id = uuid.UUID(values["launch_release_id"])
        campaign_id = uuid.UUID(values["campaign_id"])
        code_batch_id = uuid.UUID(values["code_batch_id"])
    except (TypeError, ValueError) as exc:
        raise ValueError("scan token has invalid launch authority identifiers") from exc
    digest = values["content_digest"]
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("scan token has invalid launch authority digest")
    return ScanLaunchAuthority(
        launch_release_id=release_id,
        campaign_id=campaign_id,
        code_batch_id=code_batch_id,
        content_digest=digest,
    )


def verify_scan_token(
    token: str,
    expected_public_id: str | None = None,
    expected_ip_hash: str | None = None,
    expected_tenant_id: str | None = None,
) -> dict | None:
    """验证 scan_token。

    Args:
        token: JWT 字符串
        expected_public_id: 可选，验证码标识匹配
        expected_ip_hash: 可选，验证 IP 哈希匹配（None 跳过验证）
        expected_tenant_id: 可选，验证租户 ID 匹配
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.exceptions.DecodeError:
        return None
    except jwt.exceptions.ExpiredSignatureError:
        return None

    if payload.get("type") != "scan_token":
        return None

    if expected_public_id and payload.get("public_id") != expected_public_id:
        return None

    if expected_ip_hash is not None and payload.get("ip_hash") != expected_ip_hash:
        return None

    if expected_tenant_id is not None and payload.get("tenant_id") != expected_tenant_id:
        return None

    return payload
