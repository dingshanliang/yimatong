"""AES-256-GCM 加密 + HMAC-SHA256 哈希索引

密文格式: Base64(kid(2B) || iv(12B) || ciphertext(NB) || tag(16B))
HMAC 索引: HMAC-SHA256(plaintext, pepper) → 64 hex chars
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import uuid
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
            key_bytes = bytes.fromhex(key_hex)
            if len(key_bytes) != 32:
                raise CryptoError(f"AES_MASTER_KEY_V{kid} must be 32 bytes (64 hex chars), got {len(key_bytes)}")
            self._keys[kid] = key_bytes
            self._current_kid = kid
            kid += 1

        if not self._keys:
            raise CryptoError("No AES_MASTER_KEY_Vn configured")

        pepper_hex = os.environ.get("HMAC_PEPPER", "")
        if not pepper_hex:
            raise CryptoError("HMAC_PEPPER not configured")
        pepper_bytes = bytes.fromhex(pepper_hex)
        if len(pepper_bytes) < 32:
            raise CryptoError(f"HMAC_PEPPER must be at least 32 bytes (64 hex chars), got {len(pepper_bytes)}")
        self._pepper = pepper_bytes

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


def encrypt_bytes(plaintext: bytes, *, aad: bytes) -> tuple[bytes, bytes, str]:
    """Encrypt arbitrary bytes with the active AES-GCM key and caller-bound AAD."""

    provider = _get_provider()
    kid = provider.get_current_kid()
    nonce = os.urandom(12)
    ciphertext = AESGCM(provider.get_key(kid)).encrypt(nonce, plaintext, aad)
    return ciphertext, nonce, f"aes-master-v{kid}"


def decrypt_bytes(ciphertext: bytes, *, nonce: bytes, key_id: str, aad: bytes) -> bytes:
    """Decrypt arbitrary bytes, failing closed for malformed keys or envelopes."""

    match = re.fullmatch(r"aes-master-v([1-9][0-9]*)", key_id)
    if match is None:
        raise CryptoError("Invalid key id")
    kid = int(match.group(1))
    try:
        key = _get_provider().get_key(kid)
    except KeyError as exc:
        raise CryptoError(f"Unknown key id {kid}") from exc
    if len(nonce) != 12 or len(ciphertext) < 16:
        raise CryptoError("Invalid AES-GCM envelope")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise CryptoError("Decryption failed") from exc


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
        provider.get_pepper(),
        phone.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _consumer_phone_aad(tenant_id: uuid.UUID, consumer_id: uuid.UUID) -> bytes:
    return b"consumer-phone-v2\0" + tenant_id.bytes + b"\0" + consumer_id.bytes


def _validate_consumer_phone(phone: str) -> None:
    if re.fullmatch(r"1\d{10}", phone) is None:
        raise CryptoError("Invalid consumer phone")


def encrypt_consumer_phone(
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    phone: str,
) -> tuple[bytes, bytes, str]:
    """Encrypt a consumer phone with tenant/profile identity-bound AAD."""

    _validate_consumer_phone(phone)
    return encrypt_bytes(phone.encode("ascii"), aad=_consumer_phone_aad(tenant_id, consumer_id))


def decrypt_consumer_phone(
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    ciphertext: bytes,
    nonce: bytes,
    key_id: str,
) -> str:
    """Decrypt and validate one tenant/profile-bound consumer phone envelope."""

    try:
        phone = decrypt_bytes(
            ciphertext,
            nonce=nonce,
            key_id=key_id,
            aad=_consumer_phone_aad(tenant_id, consumer_id),
        ).decode("ascii")
    except UnicodeDecodeError as exc:
        raise CryptoError("Invalid consumer phone envelope") from exc
    _validate_consumer_phone(phone)
    return phone


def _wechat_openid_lookup_material(tenant_id: uuid.UUID, openid: str) -> bytes:
    if not isinstance(openid, str) or not openid or openid.isspace() or len(openid) > 128:
        raise CryptoError("Invalid WeChat OpenID")
    return b"wechat-openid-v1\0" + tenant_id.bytes + b"\0" + openid.encode("utf-8")


def _wechat_openid_aad(tenant_id: uuid.UUID, consumer_id: uuid.UUID) -> bytes:
    return b"wechat-openid-v1\0" + tenant_id.bytes + b"\0" + consumer_id.bytes


def hash_wechat_openid(tenant_id: uuid.UUID, openid: str) -> str:
    """Return a tenant-scoped lookup digest without persisting the OpenID."""

    return hmac.new(
        _get_provider().get_pepper(),
        _wechat_openid_lookup_material(tenant_id, openid),
        hashlib.sha256,
    ).hexdigest()


def _member_identity_material(
    tenant_id: uuid.UUID,
    credential_type: str,
    issuer: str,
    subject: str,
) -> bytes:
    if credential_type not in {"verified_phone", "wechat_openid", "wechat_unionid"}:
        raise CryptoError("Invalid member identity type")
    if not issuer or len(issuer) > 160 or not subject or len(subject) > 256:
        raise CryptoError("Invalid member identity subject")
    return (
        b"member-identity-v1\0"
        + tenant_id.bytes
        + b"\0"
        + credential_type.encode("ascii")
        + b"\0"
        + issuer.encode("utf-8")
        + b"\0"
        + subject.encode("utf-8")
    )


def hash_member_identity_subject(
    tenant_id: uuid.UUID,
    credential_type: str,
    issuer: str,
    subject: str,
) -> str:
    """Return a tenant and issuer scoped lookup digest for a verified identity."""

    return hmac.new(
        _get_provider().get_pepper(),
        _member_identity_material(tenant_id, credential_type, issuer, subject),
        hashlib.sha256,
    ).hexdigest()


def _member_identity_aad(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    credential_type: str,
    issuer: str,
) -> bytes:
    return (
        b"member-identity-envelope-v1\0"
        + tenant_id.bytes
        + b"\0"
        + membership_id.bytes
        + b"\0"
        + credential_type.encode("ascii")
        + b"\0"
        + issuer.encode("utf-8")
    )


def encrypt_member_identity_subject(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    credential_type: str,
    issuer: str,
    subject: str,
) -> tuple[bytes, bytes, str]:
    _member_identity_material(tenant_id, credential_type, issuer, subject)
    return encrypt_bytes(
        subject.encode("utf-8"),
        aad=_member_identity_aad(tenant_id, membership_id, credential_type, issuer),
    )


def decrypt_member_identity_subject(
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    credential_type: str,
    issuer: str,
    ciphertext: bytes,
    nonce: bytes,
    key_id: str,
) -> str:
    try:
        subject = decrypt_bytes(
            ciphertext,
            nonce=nonce,
            key_id=key_id,
            aad=_member_identity_aad(tenant_id, membership_id, credential_type, issuer),
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CryptoError("Invalid member identity envelope") from exc
    _member_identity_material(tenant_id, credential_type, issuer, subject)
    return subject


def encrypt_wechat_openid(
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    openid: str,
) -> tuple[bytes, bytes, str]:
    """Encrypt an OpenID with tenant and consumer identity bound as AEAD AAD."""

    _wechat_openid_lookup_material(tenant_id, openid)
    return encrypt_bytes(openid.encode("utf-8"), aad=_wechat_openid_aad(tenant_id, consumer_id))


def decrypt_wechat_openid(
    tenant_id: uuid.UUID,
    consumer_id: uuid.UUID,
    ciphertext: bytes,
    nonce: bytes,
    key_id: str,
) -> str:
    """Decrypt a tenant/consumer-bound OpenID and fail closed on envelope drift."""

    try:
        openid = decrypt_bytes(
            ciphertext,
            nonce=nonce,
            key_id=key_id,
            aad=_wechat_openid_aad(tenant_id, consumer_id),
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CryptoError("Invalid WeChat OpenID envelope") from exc
    _wechat_openid_lookup_material(tenant_id, openid)
    return openid


def mask_phone(phone: str) -> str:
    """手机号脱敏：显示前3后4"""
    if len(phone) == 11:
        return phone[:3] + "****" + phone[7:]
    return "****" if phone else ""
