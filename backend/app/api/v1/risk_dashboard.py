"""渠道风控看板 API"""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.common import PaginatedResponse
from app.services.risk_dashboard import (
    export_risk_data,
    get_cross_region_stats,
    get_cross_region_trend,
    get_diversion_summary,
    get_repeat_scan_stats,
    resolve_diversion_clue,
)

risk_dashboard_router = APIRouter(prefix="/api/v1/risk-dashboard", tags=["risk-dashboard"])


def require_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")


@risk_dashboard_router.get("/repeat-scans")
async def repeat_scans_endpoint(
    min_count: int = Query(2, ge=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await get_repeat_scan_stats(db, tenant_id, min_count=min_count, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@risk_dashboard_router.get("/cross-region")
async def cross_region_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    stats = await get_cross_region_stats(db, tenant_id, days_back=days_back)
    return stats


@risk_dashboard_router.get("/cross-region-trend")
async def cross_region_trend_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    trend = await get_cross_region_trend(db, tenant_id, days_back=days_back)
    return {"trend": trend}


@risk_dashboard_router.get("/diversion-summary")
async def diversion_summary_endpoint(
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_diversion_summary(db, tenant_id, resolved=resolved, page=page, page_size=page_size)


@risk_dashboard_router.put("/diversion-clues/{clue_id}/resolve")
async def resolve_diversion_endpoint(
    clue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    clue = await resolve_diversion_clue(db, tenant_id, clue_id)
    if not clue:
        raise HTTPException(404, "Diversion clue not found")
    return {"id": str(clue.id), "resolved": clue.resolved}


@risk_dashboard_router.get("/export", response_class=PlainTextResponse)
async def export_endpoint(
    data_type: str = Query("alerts", pattern="^(alerts|diversions)$"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _: None = Depends(require_admin),
):
    csv_data = await export_risk_data(db, tenant_id, data_type)

    from app.services.export_audit import log_export

    await log_export(
        db,
        tenant_id,
        account_id,
        f"risk_{data_type}_csv",
        file_name=f"risk_{data_type}.csv",
        row_count=csv_data.count("\n") - 1 if csv_data else 0,
    )
    await db.commit()

    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=risk_{data_type}.csv"},
    )


# ── SSE 实时告警 ──────────────────────────────────────


# 每个 tenant 维护一个 SSE 客户端队列
_sse_clients: dict[str, list[asyncio.Queue]] = {}


def _broadcast_alert(tenant_id: str, alert_data: dict) -> None:
    """向所有订阅该 tenant 的 SSE 客户端广播告警"""
    queues = _sse_clients.get(tenant_id, [])
    for q in queues:
        try:
            q.put_nowait(alert_data)
        except asyncio.QueueFull:
            pass  # 丢弃过旧消息


@risk_dashboard_router.get("/alerts/stream")
async def alert_stream(
    request: Request,
    token: str = Query(..., description="JWT access token (EventSource 不支持自定义 header)"),
):
    """SSE 实时告警流。Admin 前端通过 EventSource 连接，使用 query parameter 认证。"""
    from starlette.responses import JSONResponse

    from app.utils.security import verify_access_token

    payload = await verify_access_token(token)
    if payload is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

    tid = payload.get("tenant_id")
    if not tid:
        return JSONResponse(status_code=401, content={"detail": "Tenant context not found"})

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    if tid not in _sse_clients:
        _sse_clients[tid] = []
    _sse_clients[tid].append(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        finally:
            _sse_clients[tid].remove(queue)
            if not _sse_clients[tid]:
                del _sse_clients[tid]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
