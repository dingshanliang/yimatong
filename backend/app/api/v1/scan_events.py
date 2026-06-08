"""扫码事件上报端点（H5 前端使用）"""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.scan_event import parse_environment, record_scan_event
from app.services.scan_token import verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip

scan_event_router = APIRouter(tags=["scan-events"])


class ScanEventRequest(BaseModel):
    event_type: str = "view"
    public_id: str
    page_version_id: str | None = None
    timestamp: str | None = None


@scan_event_router.post("/scan-events", status_code=201)
async def report_scan_event(
    request: Request,
    body: ScanEventRequest,
    db: AsyncSession = Depends(get_db),
):
    """H5 前端上报扫码事件（view/click 等）"""
    # 验证 scan_token（必须）
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return {"status": "ignored", "reason": "missing_token"}
    token = auth_header[7:]

    client_ip = get_client_ip(request)
    ip_hash = compute_ip_hash(client_ip)

    payload = verify_scan_token(token, body.public_id, expected_ip_hash=ip_hash)
    if payload is None:
        return {"status": "ignored", "reason": "invalid_token"}

    # 从 token payload 获取 tenant_id（已包含，无需额外查询）
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        return {"status": "ignored", "reason": "no_tenant_context"}

    user_agent = request.headers.get("user-agent")

    try:
        event = await record_scan_event(
            db=db,
            tenant_id=uuid.UUID(tenant_id),
            public_id=body.public_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            environment=parse_environment(user_agent),
        )
        return {"status": "ok", "is_first_scan": event.is_first_scan}
    except Exception:
        return {"status": "ignored", "reason": "internal_error"}
