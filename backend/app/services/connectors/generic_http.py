"""通用 HTTP 连接器适配器。

适用于自建商城等提供标准 REST API 的外部平台。
connector.config 需包含:
  - api_url: 外部 API 基地址
  - api_key: Bearer token（敏感，应存 secrets_encrypted）
"""

from __future__ import annotations

import logging

import httpx

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter

logger = logging.getLogger(__name__)


class GenericHttpAdapter(BaseConnectorAdapter):
    async def sync_stock(self, connector: Connector) -> int:
        api_url = connector.config.get("api_url", "")
        api_key = connector.config.get("api_key", "")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/stock",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("available", data.get("count", 0))

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        api_url = connector.config.get("api_url", "")
        api_key = connector.config.get("api_key", "")
        benefit_type = benefit_config.get("benefit_type", "coupon")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{api_url}/deliver",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "consumer_id": consumer_id,
                    "benefit_type": benefit_type,
                    "benefit_config": benefit_config,
                },
            )
            resp.raise_for_status()
            ext_data = resp.json()

        return DeliveryResult(
            status=ext_data.get("status", "success"),
            external_id=ext_data.get("id") or ext_data.get("code"),
            external_data=ext_data,
            message=ext_data.get("message", ""),
        )

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        import json

        try:
            data = json.loads(request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return CallbackResult(status="failed", message="Invalid JSON")

        return CallbackResult(
            external_id=data.get("id") or data.get("delivery_id"),
            status=data.get("status", "pending"),
            external_data=data,
        )

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        if not config.get("api_url"):
            return False, "api_url is required"
        return True, ""


register_adapter("generic_http", GenericHttpAdapter)
