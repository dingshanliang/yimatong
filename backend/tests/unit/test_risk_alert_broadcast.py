"""risk.alert 事件 → SSE 告警广播订阅单测。

服务层（app/services/risk.py）在告警落库后 event_bus.emit("risk.alert", ...)，
risk_dashboard 模块的订阅（init_alert_broadcaster）负责把告警写进 SSE 队列。
"""

import asyncio

from app.api.v1 import risk_dashboard as risk_dashboard_api
from app.core.event_bus import event_bus


async def test_emit_risk_alert_broadcasts_to_subscribed_tenant_queue(monkeypatch):
    risk_dashboard_api.init_alert_broadcaster()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    clients = {"tenant-1": [queue]}
    monkeypatch.setattr(risk_dashboard_api, "_sse_clients", clients)

    payload = {"alert_type": "multi_location", "public_id": "CODE-100", "detail": "多IP扫码"}
    await event_bus.emit("risk.alert", payload, "tenant-1")

    assert queue.qsize() == 1
    assert queue.get_nowait() == payload


async def test_emit_risk_alert_does_not_touch_other_tenants(monkeypatch):
    risk_dashboard_api.init_alert_broadcaster()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    clients = {"tenant-1": [queue]}
    monkeypatch.setattr(risk_dashboard_api, "_sse_clients", clients)

    await event_bus.emit(
        "risk.alert",
        {"alert_type": "suspected_copy", "public_id": "CODE-200", "detail": "高频扫码"},
        "tenant-other",
    )

    assert queue.empty()


async def test_broadcast_drops_newest_message_on_full_queue(monkeypatch):
    risk_dashboard_api.init_alert_broadcaster()

    full_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    full_queue.put_nowait({"existing": True})
    monkeypatch.setattr(risk_dashboard_api, "_sse_clients", {"tenant-2": [full_queue]})

    await event_bus.emit(
        "risk.alert",
        {"alert_type": "suspected_copy", "public_id": "CODE-300", "detail": "高频扫码"},
        "tenant-2",
    )

    # 队列满时丢弃新消息但不抛错，保留旧消息
    assert full_queue.get_nowait() == {"existing": True}


async def test_broadcast_with_no_subscribers_is_noop(monkeypatch):
    risk_dashboard_api.init_alert_broadcaster()
    monkeypatch.setattr(risk_dashboard_api, "_sse_clients", {})

    # 无订阅者时 emit 不应抛错
    await event_bus.emit(
        "risk.alert",
        {"alert_type": "multi_location", "public_id": "CODE-400", "detail": "多IP扫码"},
        "tenant-3",
    )


def test_init_alert_broadcaster_is_idempotent():
    from app.core.event_bus import event_bus as bus

    before = len(bus._handlers.get("risk.alert", []))
    risk_dashboard_api.init_alert_broadcaster()
    risk_dashboard_api.init_alert_broadcaster()
    after = len(bus._handlers.get("risk.alert", []))

    # 幂等：重复调用最多注册一次（首次 before==0 时注册 1 个，之后不再增长）
    assert after == max(before, 1)
