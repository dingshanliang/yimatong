"""企业微信 CRM 连接器适配器。

通过 BaseConnectorAdapter 接口将企业微信（WeChat Work / WeCom）
的外部联系人、标签等 CRM 能力接入一码通连接器框架。

connector.config 需包含:
  （暂无强制字段，可选 future 扩展）

connector.secrets_encrypted 需包含（解密后 JSON）:
  {
      "corpid": "ww1234567890",
      "secret": "xxxxxxxxxxxxxxxx"
  }
"""

from __future__ import annotations

import logging

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter
from app.services.connectors.secrets import decrypt_secrets
from app.services.wecom_client import WeChatWorkClient, WeComAPIError

logger = logging.getLogger(__name__)


class WeChatWorkCrmAdapter(BaseConnectorAdapter):
    """企业微信 CRM 连接器适配器。

    将企微外部联系人管理、标签管理等能力桥接到一码通连接器框架。
    """

    connector_type = "wecom_crm"

    # ── 客户端工厂 ─────────────────────────────────────────────

    def get_client(self, connector: Connector) -> WeChatWorkClient:
        """从 Connector 模型创建 WeChatWorkClient 实例。

        Args:
            connector: 连接器模型实例

        Returns:
            已配置好 corpid/secret 的 WeChatWorkClient

        Raises:
            ValueError: secrets_encrypted 缺失或格式不正确
        """
        if not connector.secrets_encrypted:
            raise ValueError("connector.secrets_encrypted 未配置")

        secrets = decrypt_secrets(connector.secrets_encrypted)
        corpid = secrets.get("corpid")
        secret = secrets.get("secret")

        if not corpid or not secret:
            raise ValueError("secrets 中必须包含 corpid 和 secret")

        return WeChatWorkClient(corpid=corpid, secret=secret)

    # ── 连接测试 ───────────────────────────────────────────────

    async def test_connection(self, config: dict, secrets: dict) -> dict:
        """测试企业微信连接是否正常。

        Args:
            config: 连接器配置（暂未使用）
            secrets: 解密后的凭证 {"corpid": "...", "secret": "..."}

        Returns:
            {"success": True/False, "message": "描述信息"}
        """
        corpid = secrets.get("corpid")
        secret = secrets.get("secret")

        if not corpid or not secret:
            return {"success": False, "message": "corpid 和 secret 不能为空"}

        client = WeChatWorkClient(corpid=corpid, secret=secret)
        try:
            token = await client.get_access_token()
            if token:
                return {"success": True, "message": "企业微信连接测试成功，access_token 已获取"}
            return {"success": False, "message": "access_token 获取失败"}
        except WeComAPIError as e:
            logger.warning("企业微信连接测试失败: errcode=%d, errmsg=%s", e.errcode, e.errmsg)
            return {"success": False, "message": f"连接失败（错误码 {e.errcode}）: {e.errmsg}"}
        except Exception as e:
            logger.exception("企业微信连接测试异常")
            return {"success": False, "message": f"连接异常: {e}"}
        finally:
            await client.close()

    # ── BaseConnectorAdapter 接口实现 ──────────────────────────

    async def sync_stock(self, connector: Connector) -> int:
        """企微 CRM 不涉及库存同步，返回 -1 表示不适用。"""
        return -1

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        """企微 CRM 的「发放」操作：为外部联系人打标签/添加备注。

        benefit_config 可包含:
          - action: "mark_tag" | "add_remark" (默认 "mark_tag")
          - tags: 要添加的标签 ID 列表
          - remove_tags: 要移除的标签 ID 列表
          - remark: 备注内容
        """
        client = self.get_client(connector)
        try:
            action = benefit_config.get("action", "mark_tag")

            if action == "add_remark":
                remark = benefit_config.get("remark", "")
                if not remark:
                    return DeliveryResult(status="failed", message="remark 不能为空")
                resp = await client.add_external_contact_remark(
                    external_userid=consumer_id,
                    remark=remark,
                )
                return DeliveryResult(
                    status="success",
                    external_id=consumer_id,
                    external_data=resp,
                    message="备注添加成功",
                )

            # 默认: mark_tag
            add_tag = benefit_config.get("tags", [])
            remove_tag = benefit_config.get("remove_tags", [])
            if not add_tag and not remove_tag:
                return DeliveryResult(status="failed", message="tags 和 remove_tags 不能同时为空")

            resp = await client.mark_external_contact(
                external_userid=consumer_id,
                add_tag=add_tag,
                remove_tag=remove_tag,
            )
            return DeliveryResult(
                status="success",
                external_id=consumer_id,
                external_data=resp,
                message="标签操作成功",
            )
        except WeComAPIError as e:
            logger.error(
                "企微 CRM 发放失败: consumer_id=%s, errcode=%d, errmsg=%s",
                consumer_id,
                e.errcode,
                e.errmsg,
            )
            return DeliveryResult(
                status="failed",
                external_id=consumer_id,
                message=f"企微 API 错误（{e.errcode}）: {e.errmsg}",
            )
        except Exception as e:
            logger.exception("企微 CRM 发放异常: consumer_id=%s", consumer_id)
            return DeliveryResult(
                status="failed",
                external_id=consumer_id,
                message=f"系统异常: {e}",
            )
        finally:
            await client.close()

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        """解析企业微信回调事件。

        企微回调使用 XML 格式（需解密）或 JSON 格式，
        这里处理 JSON 格式的回调通知。

        企微回调常见事件类型:
          - change_external_contact: 外部联系人变更
          - change_external_tag: 企业标签变更
        """
        import json

        try:
            data = json.loads(request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return CallbackResult(status="failed")

        event = data.get("Event", "")
        change_type = data.get("ChangeType", "")
        external_userid = data.get("ExternalUserID") or data.get("external_userid")

        return CallbackResult(
            external_id=external_userid,
            status="success",
            external_data={
                "event": event,
                "change_type": change_type,
                "raw": data,
            },
        )

    async def verify_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> bool:
        """验证企业微信回调签名。

        企微回调签名验证需要 token + encoding_aes_key，
        暂未实现完整解密逻辑，返回 False 表示需要服务层处理。
        """
        # TODO: 实现企微回调签名验证（需要 token + encoding_aes_key）
        return False

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        """验证连接器配置是否合法。"""
        # 企微 CRM 的核心凭证在 secrets 中，config 暂无强制字段
        return True, ""


# ── 注册适配器 ─────────────────────────────────────────────────
register_adapter("wecom_crm", WeChatWorkCrmAdapter)
