"""外部权益连接器适配器包。

使用方式:
    from app.services.connectors import get_adapter, register_adapter
    adapter = get_adapter(connector)
    result = await adapter.deliver(connector, consumer_id, benefit_config)
"""

from app.services.connectors.base import BaseConnectorAdapter, CallbackResult, DeliveryResult
from app.services.connectors.registry import get_adapter, register_adapter

__all__ = [
    "BaseConnectorAdapter",
    "CallbackResult",
    "DeliveryResult",
    "get_adapter",
    "register_adapter",
]
