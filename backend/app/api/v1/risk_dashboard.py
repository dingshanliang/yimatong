"""渠道风控看板 API"""

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant, require_tenant_feature
from app.schemas.common import PaginatedResponse
from app.schemas.diversion import DiversionEvidenceCreate, DiversionTransitionRequest
from app.schemas.export import RiskExportRequest
from app.services import diversion_authority
from app.services.channel_access import channel_dependencies
from app.services.export_access import (
    CanonicalExportIdempotencyKey,
    record_authorized_prepared_export,
    require_export_auth_session,
)
from app.services.export_admission import enforce_export_rate_limit
from app.services.risk_access import risk_dependencies
from app.services.risk_dashboard import (
    export_risk_data,
    get_cross_region_stats,
    get_cross_region_trend,
    get_diversion_investigation,
    get_diversion_summary,
    get_repeat_scan_stats,
)

risk_dashboard_router = APIRouter(
    prefix="/api/v1/risk-dashboard",
    tags=["risk-dashboard"],
)
_require_risk_feature = require_tenant_feature("risk_module", db_scope="function")


CanonicalIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]


def require_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")


@risk_dashboard_router.get(
    "/repeat-scans", dependencies=[Depends(_require_risk_feature), *risk_dependencies("risk:read")]
)
async def repeat_scans_endpoint(
    min_count: int = Query(2, ge=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await get_repeat_scan_stats(db, tenant_id, min_count=min_count, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@risk_dashboard_router.get(
    "/cross-region", dependencies=[Depends(_require_risk_feature), *risk_dependencies("risk:read")]
)
async def cross_region_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    stats = await get_cross_region_stats(db, tenant_id, days_back=days_back)
    return stats


@risk_dashboard_router.get(
    "/cross-region-trend", dependencies=[Depends(_require_risk_feature), *risk_dependencies("risk:read")]
)
async def cross_region_trend_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    trend = await get_cross_region_trend(db, tenant_id, days_back=days_back)
    return {"trend": trend}


@risk_dashboard_router.get(
    "/diversion-summary",
    dependencies=[
        Depends(_require_risk_feature),
        *risk_dependencies("risk:read"),
        *channel_dependencies("channel:read"),
    ],
)
async def diversion_summary_endpoint(
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_diversion_summary(db, tenant_id, resolved=resolved, page=page, page_size=page_size)


@risk_dashboard_router.get(
    "/diversion-clues/{clue_id}/investigation",
    dependencies=[
        Depends(_require_risk_feature),
        *risk_dependencies("risk:read"),
        *channel_dependencies("channel:read"),
    ],
)
async def diversion_investigation_endpoint(
    clue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    investigation = await get_diversion_investigation(db, tenant_id, clue_id)
    if investigation is None:
        raise HTTPException(404, "Diversion clue not found")
    clue, evidence, history = investigation
    return {
        "clue_id": str(clue.id),
        "version": clue.version,
        "investigation_status": clue.investigation_status,
        "resolved": clue.resolved,
        "resolution_action": clue.resolution_action,
        "resolution_note": clue.resolution_note,
        "evidence": [
            {
                "id": str(item.id),
                "evidence_type": item.evidence_type,
                "source": item.source,
                "file_url": item.file_url,
                "description": item.description,
                "uploaded_by_account_id": str(item.uploaded_by_account_id),
                "uploaded_at": item.uploaded_at.isoformat(),
            }
            for item in evidence
        ],
        "history": [
            {
                "id": str(item.id),
                "from_status": item.from_status,
                "to_status": item.to_status,
                "changed_by_account_id": (str(item.changed_by_account_id) if item.changed_by_account_id else None),
                "changed_at": item.changed_at.isoformat(),
                "reason": item.reason,
            }
            for item in history
        ],
    }


@risk_dashboard_router.post(
    "/diversion-clues/{clue_id}/evidence",
    status_code=201,
    dependencies=[
        Depends(_require_risk_feature),
        *risk_dependencies("risk:manage"),
        *channel_dependencies("channel:manage"),
    ],
)
async def add_diversion_evidence_endpoint(
    clue_id: uuid.UUID,
    body: DiversionEvidenceCreate,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    return await diversion_authority.add_evidence(
        db,
        tenant_id,
        clue_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        evidence_type=body.evidence_type,
        file_url=str(body.file_url) if body.file_url else None,
        description=body.description,
        evidence_digest=body.evidence_digest,
        actor_id=account_id,
    )


@risk_dashboard_router.post(
    "/diversion-clues/{clue_id}/transition",
    dependencies=[
        Depends(_require_risk_feature),
        *risk_dependencies("risk:manage"),
        *channel_dependencies("channel:manage"),
    ],
)
async def transition_diversion_endpoint(
    clue_id: uuid.UUID,
    body: DiversionTransitionRequest,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    return await diversion_authority.transition_clue(
        db,
        tenant_id,
        clue_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        to_status=body.to_status,
        reason=body.reason,
        resolution_note=body.resolution_note,
        actor_id=account_id,
    )


@risk_dashboard_router.post(
    "/export",
    dependencies=[Depends(_require_risk_feature), *risk_dependencies("risk:read")],
)
async def export_endpoint(
    body: RiskExportRequest,
    idempotency_key: CanonicalExportIdempotencyKey,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    _: None = Depends(require_admin),
):
    auth_session_id = require_export_auth_session(request)
    await enforce_export_rate_limit(tenant_id, uuid.UUID(str(request.state.account_id)))
    csv_bytes = (await export_risk_data(db, tenant_id, body.data_type)).encode("utf-8-sig")
    checksum = hashlib.sha256(csv_bytes).hexdigest()
    prepared = await record_authorized_prepared_export(
        db,
        tenant_id=tenant_id,
        auth_session_id=auth_session_id,
        export_id=uuid7(),
        export_type=f"risk_{body.data_type}_csv",
        reason=body.reason,
        scope_snapshot={"data_type": body.data_type, "row_limit": 50_000},
        idempotency_key=idempotency_key,
        file_name=f"risk_{body.data_type}.csv",
        content_type="text/csv; charset=utf-8",
        row_count=max(0, csv_bytes.count(b"\n") - 1),
        checksum_sha256=checksum,
        file_size_bytes=len(csv_bytes),
    )
    await db.commit()

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=risk_{body.data_type}.csv",
            "Content-Length": str(prepared.file_size_bytes),
            "X-Content-SHA256": prepared.checksum_sha256,
            "X-Export-Id": str(prepared.export_id),
        },
    )


# ── SSE 实时告警 ──────────────────────────────────────


# 每个 tenant 维护一个 SSE 客户端队列
_sse_clients: dict[str, list[asyncio.Queue]] = {}

# SSE ticket 存储（短期，内存中，5 分钟有效）
_sse_tickets: dict[str, dict] = {}


def _broadcast_alert(tenant_id: str, alert_data: dict) -> None:
    """向所有订阅该 tenant 的 SSE 客户端广播告警"""
    queues = _sse_clients.get(tenant_id, [])
    for q in queues:
        try:
            q.put_nowait(alert_data)
        except asyncio.QueueFull:
            pass  # 丢弃过旧消息


async def _handle_risk_alert_event(event_type: str, data: dict, tenant_id: str) -> None:
    """event_bus 订阅：把服务层产生的 risk.alert 告警广播进该租户的 SSE 队列。"""
    _broadcast_alert(tenant_id, data)


_alert_broadcaster_registered = False


def init_alert_broadcaster() -> None:
    """注册 risk.alert 事件订阅（幂等），应用启动时在 lifespan 中调用。

    订阅放在 risk_dashboard 模块而不是 services/risk.py，避免 service 层
    反向依赖 API 模块；event_bus 本身无反向依赖，无循环 import。
    """
    global _alert_broadcaster_registered
    if _alert_broadcaster_registered:
        return
    from app.core.event_bus import event_bus

    event_bus.add_handler("risk.alert", _handle_risk_alert_event)
    _alert_broadcaster_registered = True


@risk_dashboard_router.post(
    "/alerts/ticket", dependencies=[Depends(_require_risk_feature), *risk_dependencies("risk:read")]
)
async def create_sse_ticket(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """为 SSE 连接创建一次性短期 ticket（替代 URL 中的 JWT）"""
    ticket = secrets.token_urlsafe(32)
    _sse_tickets[ticket] = {
        "tenant_id": str(tenant_id),
        "exp": time.time() + 300,  # 5 分钟有效
    }
    return {"ticket": ticket}


@risk_dashboard_router.get("/alerts/stream")
async def alert_stream(
    request: Request,
    ticket: str = Query(..., description="SSE ticket obtained from POST /alerts/ticket"),
    db: AsyncSession = Depends(get_db),
):
    """SSE 实时告警流。使用短期 ticket 认证，避免 JWT 暴露在 URL 中。"""
    from starlette.responses import JSONResponse

    # 验证 ticket
    ticket_data = _sse_tickets.pop(ticket, None)
    if not ticket_data or ticket_data["exp"] < time.time():
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired SSE ticket"})

    tid = ticket_data["tenant_id"]

    from app.core.database import set_session_tenant_context
    from app.services.entitlement import TenantFeatureDisabledError
    from app.services.entitlement import require_tenant_feature as require_feature

    tenant_uuid = await set_session_tenant_context(db, tid)
    try:
        await require_feature(db, tenant_uuid, "risk_module")
    except TenantFeatureDisabledError:
        return JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "code": "TENANT_FEATURE_DISABLED",
                    "feature": "risk_module",
                    "message": "当前套餐未开通此功能",
                }
            },
        )

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
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_clients[tid].remove(queue)
            if not _sse_clients[tid]:
                del _sse_clients[tid]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
