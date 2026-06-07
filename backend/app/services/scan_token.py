"""scan_token 防伪机制"""

import time

import jwt

from app.core.config import settings


def create_scan_token(public_id: str, ip_hash: str, expires_in: int = 1800) -> str:
    """颁发 scan_token（短期 JWT，默认 5 分钟）"""
    payload = {
        "public_id": public_id,
        "ip_hash": ip_hash,
        "exp": int(time.time()) + expires_in,
        "type": "scan_token",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_scan_token(token: str, expected_public_id: str | None = None, expected_ip_hash: str | None = None) -> dict | None:
    """验证 scan_token，可选验证 public_id 和 IP hash。"""
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

    # IP hash 验证（可选）
    if expected_ip_hash and payload.get("ip_hash") != expected_ip_hash:
        return None

    return payload
