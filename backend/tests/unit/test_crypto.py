"""AES-GCM 加密 + HMAC-SHA256 哈希 单元测试"""

import os

import pytest


# 在导入 crypto 之前设置测试密钥
os.environ["AES_MASTER_KEY_V1"] = "00" * 32
os.environ["HMAC_PEPPER"] = "ff" * 32

from app.utils.crypto import (
    CryptoError,
    decrypt_phone,
    encrypt_phone,
    hash_phone,
    init_crypto,
    mask_phone,
)
from app.utils.crypto import EnvKeyProvider


@pytest.fixture(autouse=True)
def _init_crypto():
    """每个测试前初始化加密模块"""
    init_crypto(EnvKeyProvider())


class TestEncryptDecrypt:
    """AES-256-GCM 加解密"""

    def test_roundtrip(self):
        """加密后解密应还原明文"""
        phone = "13800138000"
        encrypted = encrypt_phone(phone)
        assert decrypt_phone(encrypted) == phone

    def test_unique_ciphertext(self):
        """相同明文两次加密应产生不同密文（不同 iv）"""
        phone = "13900139000"
        e1 = encrypt_phone(phone)
        e2 = encrypt_phone(phone)
        assert e1 != e2
        assert decrypt_phone(e1) == phone
        assert decrypt_phone(e2) == phone

    def test_decrypt_tampered_ciphertext(self):
        """篡改密文应抛出异常"""
        encrypted = encrypt_phone("13800138000")
        tampered = encrypted[:-4] + "XXXX"
        with pytest.raises(CryptoError):
            decrypt_phone(tampered)

    def test_decrypt_invalid_base64(self):
        """无效 Base64 应抛出异常"""
        with pytest.raises(CryptoError):
            decrypt_phone("not-valid-base64!!!")

    def test_decrypt_unknown_kid(self):
        """未知 kid 应抛出异常"""
        import base64
        import struct

        # 构造 kid=999 的密文
        fake_data = struct.pack(">H", 999) + b"\x00" * 28
        fake_b64 = base64.b64encode(fake_data).decode()
        with pytest.raises(CryptoError, match="key"):
            decrypt_phone(fake_b64)

    def test_empty_phone(self):
        """空字符串应正常加解密"""
        encrypted = encrypt_phone("")
        assert decrypt_phone(encrypted) == ""


class TestHmacHash:
    """HMAC-SHA256 哈希索引"""

    def test_deterministic(self):
        """相同手机号应产生相同 hash"""
        h1 = hash_phone("13800138000")
        h2 = hash_phone("13800138000")
        assert h1 == h2

    def test_different_phones(self):
        """不同手机号应产生不同 hash"""
        h1 = hash_phone("13800138000")
        h2 = hash_phone("13900139000")
        assert h1 != h2

    def test_hex_length(self):
        """hash 应为 64 字符 hex 字符串"""
        h = hash_phone("13800138000")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestMaskPhone:
    """手机号脱敏"""

    def test_11_digit(self):
        assert mask_phone("13800138000") == "138****8000"

    def test_short_number(self):
        assert mask_phone("12345") == "****"

    def test_empty(self):
        assert mask_phone("") == ""


class TestKeyProvider:
    """EnvKeyProvider"""

    def test_load_keys(self):
        provider = EnvKeyProvider()
        assert provider.get_current_kid() == 1
        key = provider.get_key(1)
        assert len(key) == 32

    def test_unknown_kid(self):
        provider = EnvKeyProvider()
        with pytest.raises(KeyError):
            provider.get_key(999)

    def test_pepper(self):
        provider = EnvKeyProvider()
        pepper = provider.get_pepper()
        assert len(pepper) == 32


class TestNotInitialized:
    """未初始化时调用加密函数应报错"""

    def test_encrypt_without_init(self):
        import app.utils.crypto as crypto_mod

        crypto_mod._provider = None
        with pytest.raises(CryptoError, match="not initialized"):
            encrypt_phone("13800138000")

    def test_decrypt_without_init(self):
        import app.utils.crypto as crypto_mod

        crypto_mod._provider = None
        with pytest.raises(CryptoError, match="not initialized"):
            decrypt_phone("anything")
