"""微信支付商家转账到零钱（现金营销）连接器适配器。

Connector.encrypted_config（解密后）需包含:
  - oa_appid: 微信公众号 AppID
  - oa_appsecret: 微信公众号 AppSecret（用于 OAuth 换 OpenID）
  - mch_id: 微信支付商户号
  - api_v3_key: APIv3 密钥（用于回调解密）
  - cert_serial_no: 商户证书序列号
  - cert_private_key: 商户 API 私钥 PEM
  - cert_public_cert: 微信平台证书 PEM（用于验签回调）

转账场景: cash_marketing（单笔上限 200 元）
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter
from app.utils.wechat_pay import (
    decrypt_callback_resource,
    sign_with_serial,
    verify_callback_signature,
)

logger = logging.getLogger(__name__)

WECHAT_TRANSFER_BASE = "https://api.mch.weixin.qq.com"
TRANSFER_URL = "/v3/fund-app/mch-transfer/transfer-bills/transfer"
TRANSFER_DETAIL_URL = "/v3/fund-app/mch-transfer/transfer-bills/transfer-detail/{detail_id}"
SCENE_ID = "1005"  # 现金营销场景 ID

HTTP_TIMEOUT = 10.0


class WeChatPayTransferAdapter(BaseConnectorAdapter):
    """微信支付商家转账适配器（现金营销场景）。"""

    def _get_config(self, connector: Connector) -> dict:
        if connector.secrets_encrypted:
            from app.services.connectors.secrets import decrypt_secrets

            secrets = decrypt_secrets(connector.secrets_encrypted)
            return {**connector.config, **secrets}
        return connector.config

    async def sync_stock(self, connector: Connector) -> int:
        # 库存由 Benefit.config_json 管理，不需要从微信同步
        return connector.config.get("remaining", 0)

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        """调用微信支付 V3 转账 API，将钱转到用户微信零钱。"""
        cfg = self._get_config(connector)
        mch_id = cfg.get("mch_id", "")
        oa_appid = cfg.get("oa_appid", "")
        cert_serial_no = cfg.get("cert_serial_no", "")
        private_key_pem = cfg.get("cert_private_key", "")
        amount = benefit_config.get("amount", 0)
        openid = benefit_config.get("openid", "")
        out_bill_no = benefit_config.get("out_bill_no", str(uuid.uuid4()))
        remark = benefit_config.get("transfer_remark", "扫码领红包")

        if not all([mch_id, oa_appid, cert_serial_no, private_key_pem, openid, amount]):
            return DeliveryResult(
                status="failed",
                message=f"Missing required config: mch_id={bool(mch_id)}, appid={bool(oa_appid)}, "
                        f"serial={bool(cert_serial_no)}, key={bool(private_key_pem)}, "
                        f"openid={bool(openid)}, amount={bool(amount)}",
            )

        from app.core.config import settings

        notify_url = f"{settings.base_url}/api/v1/connectors/connectors/{connector.id}/callback"

        body_dict = {
            "appid": oa_appid,
            "out_bill_no": out_bill_no,
            "transfer_scene_id": SCENE_ID,
            "openid": openid,
            "transfer_amount": amount,
            "transfer_remark": remark,
            "notify_url": notify_url,
        }
        body_json = json.dumps(body_dict, ensure_ascii=False)

        url = TRANSFER_URL
        signing = sign_with_serial(private_key_pem, mch_id, cert_serial_no, "POST", url, body_json)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": signing["authorization"],
        }

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    f"{WECHAT_TRANSFER_BASE}{url}",
                    content=body_json,
                    headers=headers,
                )

            if resp.status_code == 200:
                data = resp.json()
                return DeliveryResult(
                    status="success",
                    external_id=data.get("transfer_bill_no", out_bill_no),
                    external_data=data,
                    message="Transfer succeeded",
                )
            elif resp.status_code in (429, 502, 503):
                # 可重试的错误
                return DeliveryResult(
                    status="pending",
                    external_id=out_bill_no,
                    external_data={"status_code": resp.status_code, "body": resp.text},
                    message=f"Retryable error: {resp.status_code}",
                )
            else:
                error_data = {}
                try:
                    error_data = resp.json()
                except Exception:
                    error_data = {"raw": resp.text}
                return DeliveryResult(
                    status="failed",
                    external_id=out_bill_no,
                    external_data=error_data,
                    message=f"Transfer failed: {resp.status_code}",
                )

        except httpx.TimeoutException:
            return DeliveryResult(
                status="pending",
                external_id=out_bill_no,
                external_data={},
                message="Request timeout, need to query status",
            )
        except Exception as e:
            logger.exception("WeChat Pay transfer error")
            return DeliveryResult(
                status="failed",
                external_id=out_bill_no,
                external_data={},
                message=f"Exception: {e}",
            )

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        """解析微信转账结果回调。"""
        cfg = self._get_config(connector)
        api_v3_key = cfg.get("api_v3_key", "")
        platform_cert = cfg.get("cert_public_cert", "")

        if not verify_callback_signature(platform_cert, headers, request_body):
            logger.warning("WeChat Pay callback signature verification failed")
            return CallbackResult(status="failed")

        try:
            envelope = json.loads(request_body)
            resource = envelope.get("resource", {})
            decrypted = decrypt_callback_resource(
                api_v3_key,
                resource.get("associated_data", ""),
                resource.get("nonce", ""),
                resource.get("ciphertext", ""),
            )
            return CallbackResult(
                external_id=decrypted.get("out_bill_no"),
                status="success" if decrypted.get("transfer_status") == "SUCCESS" else "failed",
                external_data=decrypted,
            )
        except Exception as e:
            logger.exception("Failed to parse WeChat Pay callback")
            return CallbackResult(status="failed", external_data={"error": str(e)})

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        required = ["oa_appid", "mch_id", "cert_serial_no", "cert_private_key"]
        missing = [k for k in required if not config.get(k)]
        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"
        return True, ""

    async def verify_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> bool:
        cfg = self._get_config(connector)
        platform_cert = cfg.get("cert_public_cert", "")
        if not platform_cert:
            return False
        return verify_callback_signature(platform_cert, headers, request_body)


register_adapter("wechat_pay_transfer", WeChatPayTransferAdapter)
