"""微信支付 V3 API 签名与验签工具。

提供 HTTP Authorization header 生成、回调签名验证、证书加载等能力。
"""

from __future__ import annotations

import base64
import json
import time
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _load_private_key(pem_str: str):
    return serialization.load_pem_private_key(pem_str.encode(), password=None)


def _load_certificate(pem_str: str):
    from cryptography import x509
    return x509.load_pem_x509_certificate(pem_str.encode())


def _build_signing_string(method: str, url: str, timestamp: str, nonce: str, body: str) -> bytes:
    parts = [method, url, timestamp, nonce, body]
    return "\n".join(parts).encode("utf-8")


def sign_with_serial(
    private_key_pem: str,
    mch_id: str,
    cert_serial_no: str,
    method: str,
    url: str,
    body: str = "",
) -> dict[str, str]:
    """生成完整的 V3 签名 Authorization header。"""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signing_string = _build_signing_string(method, url, timestamp, nonce, body)
    private_key = _load_private_key(private_key_pem)
    signature = private_key.sign(signing_string, padding.PKCS1v15(), hashes.SHA256())
    signature_b64 = base64.b64encode(signature).decode("utf-8")
    authorization = (
        f'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{mch_id}",'
        f'nonce_str="{nonce}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{cert_serial_no}",'
        f'signature="{signature_b64}"'
    )
    return {
        "authorization": authorization,
        "timestamp": timestamp,
        "nonce": nonce,
    }


def verify_callback_signature(
    wechatpay_cert_pem: str,
    headers: dict,
    body: bytes,
) -> bool:
    """验证微信支付回调签名。

    headers 需包含: Wechatpay-Timestamp, Wechatpay-Nonce, Wechatpay-Signature, Wechatpay-Serial
    """
    timestamp = headers.get("Wechatpay-Timestamp", "")
    nonce = headers.get("Wechatpay-Nonce", "")
    signature_b64 = headers.get("Wechatpay-Signature", "")

    sign_str = f"{timestamp}\n{nonce}\n{body.decode('utf-8', errors='replace')}\n"
    try:
        cert = _load_certificate(wechatpay_cert_pem)
        public_key = cert.public_key()
        sig_bytes = base64.b64decode(signature_b64)
        public_key.verify(sig_bytes, sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def decrypt_callback_resource(
    api_v3_key: str,
    associated_data: str,
    nonce: str,
    ciphertext_b64: str,
) -> dict:
    """解密微信支付回调中的加密数据 (AEAD_AES_256_GCM)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = api_v3_key.encode("utf-8")
    nonce_bytes = nonce.encode("utf-8")
    ciphertext = base64.b64decode(ciphertext_b64)
    aad = associated_data.encode("utf-8") if associated_data else b""

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce_bytes, ciphertext, aad)
    return json.loads(plaintext.decode("utf-8"))


def get_cert_serial_no(cert_pem: str) -> str:
    """从证书 PEM 中提取序列号。"""
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return format(cert.serial_number, 'X')
