"""进程内事件总线。

service 层通过 event_bus.emit("event_type", data, tenant_id) 发射事件，
dispatcher 通过 @event_bus.on("event_type") 注册处理器。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[..., Coroutine[Any, Any, Any]]


class _EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        """装饰器：注册事件处理器。"""

        def decorator(fn: Handler) -> Handler:
            self._handlers[event_type].append(fn)
            return fn

        return decorator

    def add_handler(self, event_type: str, handler: Handler) -> None:
        """编程式注册事件处理器。"""
        self._handlers[event_type].append(handler)

    async def emit(self, event_type: str, data: dict, tenant_id: str) -> None:
        """发射事件，异步调用所有注册的处理器。

        处理器异常会被捕获并记录日志，不会阻塞调用方。
        """
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(event_type, data, tenant_id)
            except Exception:
                logger.exception("Event handler %s failed for event %s", handler.__name__, event_type)


event_bus = _EventBus()
