"""扫码事件上报端点（H5 前端使用）"""

import hashlib
import hmac
import logging
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, lock_active_tenant_context
from app.services.intent_event import insert_intent_event_idempotent
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.services.scan_token import verify_scan_token
from app.utils.client_ip import compute_ip_hash, get_client_ip

logger = logging.getLogger(__name__)

scan_event_router = APIRouter(tags=["scan-events"])

_RATE_WINDOW_SECONDS = 60
_IP_MAX_EVENTS = 120
_TOKEN_TENANT_MAX_EVENTS = 60
_security_cache = AsyncRedisCache(prefix="scan_event_security", default_ttl=_RATE_WINDOW_SECONDS)


class ScanEventType(StrEnum):
    view = "view"
    click = "click"
    lead_click = "lead_click"
    wecom_click = "wecom_click"
    mall_redirect = "mall_redirect"


class ScanEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: ScanEventType = ScanEventType.view
    public_id: str = Field(min_length=1, max_length=20)
    page_version_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9:_-]+$")
    timestamp: AwareDatetime | None = None
    # 所有允许的客户端事件都必须提供幂等键。action/value 等未定义维度
    # 会被 extra=forbid 拒绝，避免任意高基数数据进入分析表。
    client_event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9:_-]+$")


def _keyed_rate_digest(value: str) -> str:
    key = (settings.hmac_pepper or settings.secret_key).encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


async def _check_shared_rate_limit(key: str, max_events: int) -> JSONResponse | None:
    try:
        allowed, _ = await _security_cache.rate_limit_check_shared(
            key,
            max_attempts=max_events,
            window_seconds=_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="扫码事件服务暂时不可用，请稍后重试") from exc
    if allowed:
        return None
    return JSONResponse(
        status_code=429,
        content={"detail": "扫码事件上报过于频繁，请稍后重试"},
        headers={"Retry-After": str(_RATE_WINDOW_SECONDS)},
    )


@scan_event_router.post("/scan-events", status_code=201)
async def report_scan_event(
    request: Request,
    body: ScanEventRequest,
    db: AsyncSession = Depends(get_db),
):
    """H5 前端 view/click 行为埋点端点。

    yimatong-zgb1.10：持久化意图事件到 intent_events 表（Decision 19）。
    - 事件类型映射：view → page_view, click → benefit_click（默认）。
    - 幂等：(tenant_id, client_event_id) 由数据库原子唯一索引保证。
    - 不创建 ScanEvent 行（权威扫码事实由 resolver 唯一写入，yimatong-zgb1.4）。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return {"status": "ignored", "reason": "missing_token"}
    token = auth_header[7:]

    # request.client is the trusted peer after the ASGI server's trusted-proxy
    # processing. Caller-controlled forwarding headers are not rate-limit keys.
    trusted_client_ip = request.client.host if request.client else "unknown"
    limited = await _check_shared_rate_limit(
        f"ip:{_keyed_rate_digest(trusted_client_ip)}",
        _IP_MAX_EVENTS,
    )
    if limited is not None:
        return limited

    client_ip = get_client_ip(request)
    ip_hash = compute_ip_hash(client_ip)
    user_agent = request.headers.get("user-agent")

    payload = verify_scan_token(token, body.public_id, expected_ip_hash=ip_hash)
    if payload is None:
        return {"status": "ignored", "reason": "invalid_token"}

    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        return {"status": "ignored", "reason": "no_tenant_context"}

    token_tenant_digest = _keyed_rate_digest(f"{tenant_id}:{token}")
    limited = await _check_shared_rate_limit(
        f"token_tenant:{token_tenant_digest}",
        _TOKEN_TENANT_MAX_EVENTS,
    )
    if limited is not None:
        return limited

    from app.services.entitlement import (
        PLAN_EXPIRED_CODE,
        PLAN_EXPIRED_DETAIL,
        TenantPlanExpiredError,
    )

    try:
        tenant_uuid = await lock_active_tenant_context(db, tenant_id)
    except ValueError:
        return {"status": "ignored", "reason": "invalid_tenant_context"}
    except TenantPlanExpiredError:
        return JSONResponse(
            status_code=403,
            content={"code": PLAN_EXPIRED_CODE, "detail": PLAN_EXPIRED_DETAIL},
        )

    # 读取 visitor_id（H5 X-Visitor-ID 头）
    visitor_id = request.headers.get("X-Visitor-ID")

    event_type_map = {ScanEventType.view: "page_view", ScanEventType.click: "benefit_click"}
    normalized_type = event_type_map.get(body.event_type, body.event_type.value)
    try:
        event_id = await insert_intent_event_idempotent(
            db,
            tenant_id=tenant_uuid,
            event_type=normalized_type,
            public_id=body.public_id,
            visitor_id=visitor_id,
            client_event_id=body.client_event_id,
            page_version_id=body.page_version_id,
            ip_hash=ip_hash,
            user_agent=user_agent[:500] if user_agent else None,
            occurred_at=body.timestamp,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Failed to persist intent event", exc_info=True)
        return {"status": "ok", "persisted": False}

    if event_id is None:
        return {"status": "ok", "persisted": True, "deduplicated": True}
    return {"status": "ok", "persisted": True, "deduplicated": False, "event_id": str(event_id)}
