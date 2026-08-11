"""券码池连接器适配器。

connector.config 需包含:
  - pool_id: CouponPool 的 UUID

发放时从 CouponPool 分配一个未使用的 CouponCode。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import register_adapter

logger = logging.getLogger(__name__)


class CouponPoolAdapter(BaseConnectorAdapter):
    """券码池适配器，从本地 CouponPool 分配券码。"""

    def _get_pool_id(self, connector: Connector) -> uuid.UUID | None:
        pool_id_str = connector.config.get("pool_id")
        if not pool_id_str:
            return None
        return uuid.UUID(pool_id_str)

    async def sync_stock(self, connector: Connector) -> int:
        # 券码池不需要外部同步，但可以通过 db session 查询 remaining
        # 注意：sync_stock 在服务层调用时会传入 db，这里无法直接访问
        # 返回 config 中缓存的值，服务层会更新
        return connector.config.get("remaining", 0)

    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        # deliver() 在服务层调用，通过 db session 操作
        # 此处由服务层的 _deliver_from_coupon_pool() 处理
        # 适配器的 deliver 在券码池场景下不走此路径
        raise NotImplementedError("Use deliver_from_pool() instead")

    async def deliver_from_pool(
        self,
        db: AsyncSession,
        connector: Connector,
        consumer_id: str,
        claim_id: uuid.UUID | None = None,
    ) -> DeliveryResult:
        """从券码池分配券码。"""
        pool_id = self._get_pool_id(connector)
        if not pool_id:
            return DeliveryResult(status="failed", message="pool_id not configured")

        from app.services.connector import distribute_coupon

        code = await distribute_coupon(
            db,
            connector.tenant_id,
            pool_id,
            consumer_id,
            claim_id=claim_id,
        )
        if not code:
            return DeliveryResult(status="failed", message="No available codes in pool")

        return DeliveryResult(
            status="success",
            external_id=code.code,
            external_data={"code": code.code, "pool_id": str(pool_id)},
            message="Code assigned",
        )

    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        # 券码池不需要回调
        return CallbackResult()

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        pool_id = config.get("pool_id")
        if not pool_id:
            return False, "pool_id is required"
        try:
            uuid.UUID(pool_id)
        except ValueError:
            return False, "pool_id must be a valid UUID"
        return True, ""


register_adapter("coupon_pool", CouponPoolAdapter)
