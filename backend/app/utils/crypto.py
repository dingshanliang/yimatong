"""AES-256-GCM 加密 + HMAC-SHA256 哈希索引

密文格式: Base64(kid(2B) || iv(12B) || ciphertext(NB) || tag(16B))
HMAC 索引: HMAC-SHA256(plaintext, pepper) → 64 hex chars
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoError(Exception):
    """加密/解密错误"""


class KeyProvider(Protocol):
    def get_current_kid(self) -> int: ...
    def get_key(self, kid: int) -> bytes: ...
    def get_pepper(self) -> bytes: ...


class EnvKeyProvider:
    """从环境变量读取密钥"""

    def __init__(self):
        self._keys: dict[int, bytes] = {}
        self._current_kid: int = 1
        self._pepper: bytes = b""
        self._load()

    def _load(self):
        kid = 1
        while True:
            key_hex = os.environ.get(f"AES_MASTER_KEY_V{kid}", "")
            if not key_hex:
                break
            self._keys[kid] = bytes.fromhex(key_hex)
            self._current_kid = kid
            kid += 1

        if not self._keys:
            raise CryptoError("No AES_MASTER_KEY_Vn configured")

        pepper_hex = os.environ.get("HMAC_PEPPER", "")
        if not pepper_hex:
            raise CryptoError("HMAC_PEPPER not configured")
        self._pepper = bytes.fromhex(pepper_hex)

    def get_current_kid(self) -> int:
        return self._current_kid

    def get_key(self, kid: int) -> bytes:
        if kid not in self._keys:
            raise KeyError(f"Unknown key id: {kid}")
        return self._keys[kid]

    def get_pepper(self) -> bytes:
        return self._pepper


_provider: KeyProvider | None = None


def init_crypto(provider: KeyProvider):
    global _provider
    _provider = provider


def _get_provider() -> KeyProvider:
    if _provider is None:
        raise CryptoError("Crypto not initialized. Call init_crypto() first.")
    return _provider


def encrypt_phone(plaintext: str) -> str:
    """加密手机号，返回 Base64 编码密文"""
    provider = _get_provider()
    kid = provider.get_current_kid()
    key = provider.get_key(kid)

    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # AESGCM.encrypt 返回 ciphertext || tag (后16字节为 tag)
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    raw = struct.pack(">H", kid) + iv + ciphertext + tag
    return base64.b64encode(raw).decode("ascii")


def decrypt_phone(ciphertext_b64: str) -> str:
    """解密手机号"""
    provider = _get_provider()
    try:
        raw = base64.b64decode(ciphertext_b64)
    except Exception as e:
        raise CryptoError(f"Invalid base64: {e}") from e

    if len(raw) < 2 + 12 + 16:
        raise CryptoError("Ciphertext too short")

    kid = struct.unpack(">H", raw[:2])[0]
    iv = raw[2:14]
    ciphertext = raw[14:-16]
    tag = raw[-16:]

    try:
        key = provider.get_key(kid)
    except KeyError as e:
        raise CryptoError(f"Unknown key id {kid}") from e

    aesgcm = AESGCM(key)
    ciphertext_with_tag = ciphertext + tag
    try:
        plaintext = aesgcm.decrypt(iv, ciphertext_with_tag, None)
    except Exception as e:
        raise CryptoError(f"Decryption failed: {e}") from e

    return plaintext.decode("utf-8")


def hash_phone(phone: str) -> str:
    """HMAC-SHA256 哈希，返回 64 字符 hex"""
    provider = _get_provider()
    return hmac.new(
        provider.get_pepper(), phone.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def mask_phone(phone: str) -> str:
    """手机号脱敏：显示前3后4"""
    if len(phone) == 11:
        return phone[:3] + "****" + phone[7:]
    return "****" if phone else ""
