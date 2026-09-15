"""连接器适配器抽象基类与数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connector import Connector


@dataclass
class DeliveryResult:
    """适配器 deliver() 的返回值。"""

    status: str  # "success" | "pending" | "failed"
    external_id: str | None = None
    external_data: dict = field(default_factory=dict)
    message: str = ""


@dataclass
class CallbackResult:
    """适配器 parse_callback() 的返回值。

    status="ignored" 表示事件与本适配器的发放结算无关（端点直接 200，不结算、不 422）；
    此时可选携带 coupon_transition（如 "external_consume"）与 external_coupon_ref，
    由回调端点转交钱包权威函数做外部核销状态回流。
    """

    external_id: str | None = None
    status: str = "pending"  # "success" | "pending" | "failed" | "ignored"
    external_data: dict = field(default_factory=dict)
    coupon_transition: str | None = None
    external_coupon_ref: str | None = None


class BaseConnectorAdapter(ABC):
    """连接器适配器抽象基类。

    每个外部平台实现此接口，通过 register_adapter() 注册。
    """

    @abstractmethod
    async def sync_stock(self, connector: Connector) -> int:
        """从外部同步库存，返回可用数量。"""

    @abstractmethod
    async def deliver(
        self,
        connector: Connector,
        consumer_id: str,
        benefit_config: dict,
    ) -> DeliveryResult:
        """发放权益，返回外部系统的响应数据。"""

    @abstractmethod
    async def parse_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> CallbackResult:
        """解析外部回调，统一为内部格式。"""

    @abstractmethod
    async def validate_config(self, config: dict) -> tuple[bool, str]:
        """验证连接器配置是否合法，返回 (is_valid, error_message)。"""

    async def verify_callback(
        self,
        connector: Connector,
        request_body: bytes,
        headers: dict,
    ) -> bool:
        """验证回调请求的签名/来源。默认拒绝，子类必须覆盖以启用回调。"""
        return False

    async def prepare(self, db: AsyncSession, connector: Connector) -> Connector:
        """发放/同步前的准备钩子（如 OAuth token 检查与刷新回写）。

        默认原样返回；需要凭证生命周期管理的适配器（youzan、weimob 等）覆盖此方法。
        返回的 connector 供调用方构造 runtime 视图。
        """
        return connector

    async def callback_ack_payload(self, callback_result: CallbackResult) -> dict | None:
        """回调成功处理（ignored 或结算完成）后的响应体覆盖钩子。

        默认 None（端点返回统一内部形状）；外部平台对回调 ACK 响应有固定
        契约时覆盖（如 weimob 要求 {"code":{"errcode":0,...}}，否则重推）。
        仅影响 2xx 响应体，403/422 拒绝路径不走此钩子。
        """
        return None

    async def on_delivery_success(
        self,
        db: AsyncSession,
        *,
        tenant_id,
        claim_id,
        delivery_id,
        external_id: str,
        consumer_id: str,
        benefit_config: dict,
    ) -> None:
        """发放结算成功后的适配器钩子（如外部券写入消费者钱包）。

        默认无操作；调用方在记录发放结果成功后调用，与主结果同事务提交。
        """
        return None
