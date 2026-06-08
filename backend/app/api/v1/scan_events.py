"""扫码事件上报端点（H5 前端使用）"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.scan_event import parse_environment, record_scan_event
from app.services.scan_token import verify_scan_token

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
    # Compute IP hash for verification
    raw_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    client_ip_for_token = raw_ip.split(",")[0].strip()
    ip_hash_for_token = (
        hashlib.sha256(client_ip_for_token.encode()).hexdigest()
        if client_ip_for_token != "unknown"
        else None
    )
    payload = verify_scan_token(token, body.public_id, expected_ip_hash=ip_hash_for_token)
    if payload is None:
        return {"status": "ignored", "reason": "invalid_token"}

    # 获取客户端信息
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else None

    # 解析码数据获取 tenant_id
    from app.services.resolver import resolve_public_code

    data = await resolve_public_code(db, body.public_id)
    if not data:
        return {"status": "ignored", "reason": "code_not_found"}

    try:
        event = await record_scan_event(
            db=db,
            tenant_id=uuid.UUID(data["tenant_id"]),
            public_id=body.public_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            environment=parse_environment(user_agent),
        )
        return {"status": "ok", "is_first_scan": event.is_first_scan}
    except Exception:
        return {"status": "ignored", "reason": "internal_error"}
