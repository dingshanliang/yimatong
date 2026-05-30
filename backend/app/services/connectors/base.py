"""连接器适配器抽象基类与数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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
    """适配器 parse_callback() 的返回值。"""

    external_id: str | None = None
    status: str = "pending"  # "success" | "pending" | "failed"
    external_data: dict = field(default_factory=dict)


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
