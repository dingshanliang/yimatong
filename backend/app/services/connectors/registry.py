"""适配器注册表。

使用 register_adapter() 注册类型 → 适配器类的映射，
使用 get_adapter() 根据 Connector.connector_type 获取适配器实例。
"""

from __future__ import annotations

from app.models.connector import Connector
from app.services.connectors.base import BaseConnectorAdapter

_registry: dict[str, type[BaseConnectorAdapter]] = {}


def register_adapter(connector_type: str, adapter_cls: type[BaseConnectorAdapter]) -> None:
    if connector_type in _registry:
        raise ValueError(f"Adapter already registered for type: {connector_type}")
    _registry[connector_type] = adapter_cls


def get_adapter(connector: Connector) -> BaseConnectorAdapter:
    cls = _registry.get(connector.connector_type)
    if not cls:
        raise ValueError(f"Unknown connector type: {connector.connector_type}")
    return cls()


def list_adapter_types() -> list[str]:
    return sorted(_registry.keys())
