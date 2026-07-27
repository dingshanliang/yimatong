"""扫码事件上报端点（H5 前端使用）"""

import logging
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.intent_event import IntentEvent
from app.services.scan_token import verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip

logger = logging.getLogger(__name__)

scan_event_router = APIRouter(tags=["scan-events"])


class ScanEventRequest(BaseModel):
    event_type: str = "view"
    public_id: str
    page_version_id: str | None = None
    timestamp: str | None = None
    # yimatong-zgb1.10：客户端幂等键（防重复上报）
    client_event_id: str | None = None


@scan_event_router.post("/scan-events", status_code=201)
async def report_scan_event(
    request: Request,
    body: ScanEventRequest,
    db: AsyncSession = Depends(get_db),
):
    """H5 前端 view/click 行为埋点端点。

    yimatong-zgb1.10：持久化意图事件到 intent_events 表（Decision 19）。
    - 事件类型映射：view → page_view, click → benefit_click（默认）。
    - 幂等：(tenant_id, visitor_id, event_type, client_event_id) 唯一。
    - 不创建 ScanEvent 行（权威扫码事实由 resolver 唯一写入，yimatong-zgb1.4）。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return {"status": "ignored", "reason": "missing_token"}
    token = auth_header[7:]

    client_ip = get_client_ip(request)
    ip_hash = compute_ip_hash(client_ip)
    user_agent = request.headers.get("user-agent")

    payload = verify_scan_token(token, body.public_id, expected_ip_hash=ip_hash)
    if payload is None:
        return {"status": "ignored", "reason": "invalid_token"}

    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        return {"status": "ignored", "reason": "no_tenant_context"}

    # 读取 visitor_id（H5 X-Visitor-ID 头）
    visitor_id = request.headers.get("X-Visitor-ID")

    # 事件类型规范化
    event_type_map = {"view": "page_view", "click": "benefit_click"}
    normalized_type = event_type_map.get(body.event_type, body.event_type)

    # 幂等检查：同一 (tenant, visitor, type, client_event_id) 已存在则跳过
    if visitor_id and body.client_event_id:
        from sqlalchemy import select

        existing = await db.execute(
            select(IntentEvent.id).where(
                IntentEvent.tenant_id == uuid.UUID(tenant_id),
                IntentEvent.visitor_id == visitor_id,
                IntentEvent.event_type == normalized_type,
                IntentEvent.client_event_id == body.client_event_id,
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "ok", "deduplicated": True}

    # 持久化意图事件
    event = IntentEvent(
        tenant_id=uuid.UUID(tenant_id),
        event_type=normalized_type,
        public_id=body.public_id,
        visitor_id=visitor_id,
        client_event_id=body.client_event_id,
        page_version_id=body.page_version_id,
        ip_hash=ip_hash,
        user_agent=user_agent[:500] if user_agent else None,
    )
    db.add(event)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Failed to persist intent event", exc_info=True)
        return {"status": "ok", "persisted": False}

    return {"status": "ok", "persisted": True, "event_id": str(event.id)}
