"""连接器凭证加密工具。

复用项目已有的 AES-256-GCM 加密基础设施（app.utils.crypto），
使用同样的 EnvKeyProvider 和 AES_MASTER_KEY_Vn 环境变量。
"""

from __future__ import annotations

import json
import os
import struct
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretsError(Exception):
    """凭证加密/解密错误"""


SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "api_v3_key",
        "callback_secret",
        "cert_private_key",
        "mch_key",
        "oa_appsecret",
        "secret",
    }
)


def sensitive_config_keys(config: Mapping[str, object]) -> set[str]:
    """Return credential-like top-level keys that must never live in public config."""

    return {key for key in config if key.lower() in SENSITIVE_CONFIG_KEYS}


def public_connector_config(config: Mapping[str, object] | None) -> dict:
    """Strip legacy plaintext credentials before persistence or serialization."""

    return {key: value for key, value in dict(config or {}).items() if key.lower() not in SENSITIVE_CONFIG_KEYS}


def connector_with_runtime_secrets(connector):
    """Build a transient adapter view without dirtying the persisted ORM config."""

    from app.models.connector import Connector

    secrets = decrypt_secrets(connector.secrets_encrypted) if connector.secrets_encrypted else {}
    return Connector(
        id=connector.id,
        tenant_id=connector.tenant_id,
        name=connector.name,
        connector_type=connector.connector_type,
        config={**public_connector_config(connector.config), **secrets},
        secrets_encrypted=connector.secrets_encrypted,
        enabled=connector.enabled,
    )


def _get_key() -> tuple[int, bytes]:
    """从环境变量获取当前加密密钥。"""
    kid = 1
    key_hex = os.environ.get(f"AES_MASTER_KEY_V{kid}", "")
    if not key_hex:
        raise SecretsError("AES_MASTER_KEY_V1 not configured")
    return kid, bytes.fromhex(key_hex)


def encrypt_secrets(secrets: dict, *, associated_data: bytes | None = None) -> bytes:
    """加密凭证字典，返回二进制密文。"""
    if not secrets:
        return b""

    kid, key = _get_key()
    iv = os.urandom(12)
    plaintext = json.dumps(secrets, ensure_ascii=False).encode("utf-8")
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext, associated_data)
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    return struct.pack(">H", kid) + iv + ciphertext + tag


def decrypt_secrets(encrypted: bytes, *, associated_data: bytes | None = None) -> dict:
    """解密二进制密文，返回凭证字典。"""
    if not encrypted:
        return {}

    if len(encrypted) < 2 + 12 + 16:
        raise SecretsError("Ciphertext too short")

    kid = struct.unpack(">H", encrypted[:2])[0]
    key_hex = os.environ.get(f"AES_MASTER_KEY_V{kid}", "")
    if not key_hex:
        raise SecretsError(f"AES_MASTER_KEY_V{kid} not configured")

    key = bytes.fromhex(key_hex)
    iv = encrypted[2:14]
    ciphertext = encrypted[14:-16]
    tag = encrypted[-16:]

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(iv, ciphertext + tag, associated_data)
    except Exception as exc:
        raise SecretsError(f"Decryption failed: {exc}") from exc

    return json.loads(plaintext.decode("utf-8"))


def mask_secrets(secrets: dict) -> dict:
    """脱敏凭证字典，用于 API 返回。"""
    masked = {}
    for key, value in secrets.items():
        s = str(value)
        if len(s) <= 8:
            masked[key] = "***"
        else:
            masked[key] = s[:3] + "***" + s[-3:]
    return masked
