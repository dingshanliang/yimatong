"""scan_token 防伪机制"""

import time
import uuid

import jwt

from app.core.config import settings


def create_scan_token(
    public_id: str,
    ip_hash: str | None,
    tenant_id: str = "",
    consumer_id: str = "",
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
        "public_id": public_id,
        "ip_hash": ip_hash,
        "tenant_id": tenant_id,
        "consumer_id": consumer_id,
        "exp": int(time.time()) + expires_in,
        "type": "scan_token",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


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
