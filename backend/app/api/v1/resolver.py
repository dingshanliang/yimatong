"""码解析公开路由"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.middleware.rate_limit import rate_limiter
from app.models.code import CodeItemStatus, CodeType
from app.models.scan import ScanEvent
from app.services.page_render import render_page
from app.services.page_templates import (
    INNER_VERIFY_PAGE,
    NOT_ACTIVE_PAGE,
    NOT_FOUND_PAGE,
    OUTER_LANDING_PAGE,
    REVOKED_PAGE,
    RISK_FROZEN_PAGE,
    build_code_page,
)
from app.services.public_id import validate_public_id
from app.services.resolve_cache import resolve_cache
from app.services.resolver import resolve_public_code
from app.services.resolver_response import build_json_response
from app.services.scan_event import parse_environment, record_scan_event
from app.services.scan_token import create_scan_token

resolver_router = APIRouter(tags=["resolver"])


@resolver_router.get("/c/{public_id}", summary="解析码")
async def resolve_code_endpoint(
    public_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    accept = request.headers.get("accept", "")
    want_json = "application/json" in accept

    # 1. 限流 + 格式校验
    client_ip = request.client.host if request.client else "unknown"
    rate_result = rate_limiter.check_resolver(client_ip, public_id)
    if not rate_result.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(rate_result.retry_after)},
        )

    if not validate_public_id(public_id):
        return _not_found(want_json)

    # 2. 缓存查询 -> DB 回退
    cached = await resolve_cache.get(f"resolve:{public_id}")
    data = cached or await resolve_public_code(db, public_id)
    if data and not cached:
        await resolve_cache.set(f"resolve:{public_id}", data)

    if not data:
        return _not_found(want_json)

    status = data["status"]

    # 3. 错误状态
    if status in (CodeItemStatus.revoked, CodeItemStatus.frozen, CodeItemStatus.created):
        return _error_status(status, public_id, want_json)

    # 4. 记录扫码事件 + 生成 scan_token
    user_agent = request.headers.get("user-agent", "")
    scan_info = await _record_scan(db, data, public_id, client_ip, user_agent, status)
    ip_hash_val = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else ""
    scan_token = create_scan_token(public_id, ip_hash_val)

    # 5. JSON 模式
    if want_json:
        resp = await build_json_response(db, data, scan_token, scan_info)
        return JSONResponse(content=resp)

    # 6. HTML 模式
    return await _html_response(db, data, public_id)


# -- 内部辅助函数 --


def _not_found(want_json: bool):
    if want_json:
        return JSONResponse(status_code=404, content={"detail": "not_found"})
    return HTMLResponse(content=NOT_FOUND_PAGE, status_code=404)


def _error_status(status: str, public_id: str, want_json: bool):
    if status == CodeItemStatus.revoked:
        if want_json:
            return JSONResponse(status_code=410, content={"code_data": {"status": "revoked", "public_id": public_id}})
        return HTMLResponse(content=REVOKED_PAGE, status_code=410)

    if status == CodeItemStatus.frozen:
        if want_json:
            return JSONResponse(status_code=403, content={"code_data": {"status": "frozen", "public_id": public_id}})
        return HTMLResponse(content=RISK_FROZEN_PAGE, status_code=403)

    # CodeItemStatus.created
    if want_json:
        return JSONResponse(content={"code_data": {"status": "not_active", "public_id": public_id}})
    return HTMLResponse(content=NOT_ACTIVE_PAGE, status_code=200)


async def _record_scan(
    db: AsyncSession,
    data: dict,
    public_id: str,
    client_ip: str,
    user_agent: str,
    status: str,
) -> dict:
    scan_info: dict = {"is_first_scan": False, "scan_count": 0}
    if status != CodeItemStatus.activated:
        return scan_info

    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip != "unknown" else None
    try:
        count_before = await db.execute(
            select(func.count()).select_from(ScanEvent).where(ScanEvent.public_id == public_id)
        )
        scan_info["scan_count"] = count_before.scalar() or 0
        event = await record_scan_event(
            db=db,
            tenant_id=uuid.UUID(data["tenant_id"]),
            public_id=public_id,
            ip_hash=ip_hash,
            user_agent=user_agent,
            environment=parse_environment(user_agent),
        )
        scan_info["is_first_scan"] = event.is_first_scan
    except Exception:
        pass
    return scan_info


async def _html_response(db: AsyncSession, data: dict, public_id: str):
    code_type = data.get("code_type", CodeType.single)

    if code_type == CodeType.outer:
        return HTMLResponse(content=OUTER_LANDING_PAGE.format(public_id=public_id))

    if code_type == CodeType.inner:
        template_id = data.get("template_id")
        tenant_id = data.get("tenant_id")
        if template_id and tenant_id:
            html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
            if html:
                return HTMLResponse(content=html)
        return HTMLResponse(content=INNER_VERIFY_PAGE.format(public_id=public_id))

    # single 类型：尝试渲染页面模板，否则降级
    template_id = data.get("template_id")
    tenant_id = data.get("tenant_id")
    if template_id and tenant_id:
        html = await render_page(db, uuid.UUID(tenant_id), uuid.UUID(template_id))
        if html:
            return HTMLResponse(content=html)

    return HTMLResponse(content=build_code_page(data))
