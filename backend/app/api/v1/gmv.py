"""外部成交与 GMV 归因 API"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.common import PaginatedResponse
from app.schemas.gmv import (
    AutoAttributionRequest,
    ConfirmedAttributionRequest,
    ExternalOrderCancelRequest,
    ExternalOrderImportRequest,
    ExternalOrderRefundRequest,
    GmvDashboardResponse,
    GmvRoiItem,
)
from app.services.gmv import (
    aggregate_daily_stats,
    batch_auto_attribution,
    confirm_gmv_attribution,
    get_gmv_dashboard,
    get_roi_report,
    list_attributions,
    list_orders,
)
from app.services.gmv_access import (
    authenticated_import_provenance_digest,
    enforce_order_mutation_rate_limit,
    order_dependencies,
    require_direct_brand_order_principal,
)
from app.services.gmv_authority import (
    canonical_order_payload_digest,
    derive_order_event_idempotency_key,
    record_external_order_value_event,
)

gmv_router = APIRouter(prefix="/api/v1/gmv", tags=["gmv"])


CanonicalIdempotencyKey = Annotated[
    uuid.UUID,
    Header(alias="Idempotency-Key"),
]


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)


def _authority_http_error(exc: DBAPIError) -> HTTPException:
    response = {
        "23503": (404, "External order not found", None),
        "23505": (409, "Idempotency-Key was already used with different order data", None),
        "22023": (409, "Invalid or terminal external order transition", None),
        "42501": (403, "External order authority denied", None),
        "55P03": (409, "External order is busy; retry the request", {"Retry-After": "1"}),
    }.get(_sqlstate(exc))
    if response is None:
        raise exc
    status_code, detail, headers = response
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def _attribution_authority_http_error(exc: DBAPIError) -> HTTPException:
    response = {
        "23503": (404, "Confirmed order or consumer-bound scan evidence not found"),
        "23505": (409, "Attribution already confirmed or Idempotency-Key payload conflicts"),
        "22023": (409, "Order is outside the snapshotted attribution window"),
        "42501": (403, "GMV attribution authority denied"),
        "55P03": (409, "Attribution is busy; retry the request"),
    }.get(_sqlstate(exc))
    if response is None:
        raise exc
    status_code, detail = response
    headers = {"Retry-After": "1"} if _sqlstate(exc) == "55P03" else None
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


@gmv_router.post(
    "/orders/import",
    summary="导入可信外部订单",
    dependencies=order_dependencies("order:manage"),
)
async def import_orders_endpoint(
    body: ExternalOrderImportRequest,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    auth_session_id: uuid.UUID = Depends(require_direct_brand_order_principal),
):
    await enforce_order_mutation_rate_limit(tenant_id, account_id, body.source_system)
    provenance_digest = authenticated_import_provenance_digest(tenant_id, body.source_system, auth_session_id)
    result: dict = {"created": 0, "replayed": 0, "failed": 0, "errors": [], "items": []}
    for index, order in enumerate(body.orders):
        payload = {
            "event_type": "order_confirmed",
            "source_system": body.source_system,
            **order.model_dump(mode="python"),
        }
        per_order_key = derive_order_event_idempotency_key(
            idempotency_key,
            body.source_system,
            order.external_id,
        )
        try:
            async with db.begin_nested():
                recorded = await record_external_order_value_event(
                    db,
                    tenant_id=tenant_id,
                    source_system=body.source_system,
                    external_order_id=order.external_id,
                    event_type="order_confirmed",
                    amount=order.amount,
                    currency=order.currency,
                    idempotency_key=per_order_key,
                    payload_digest=canonical_order_payload_digest(payload),
                    provenance_digest=provenance_digest,
                    auth_session_id=auth_session_id,
                    occurred_at=order.order_time,
                    order_time=order.order_time,
                    phone=order.phone,
                    product_name=order.product_name,
                    channel=order.channel,
                )
        except DBAPIError as exc:
            mapped = _authority_http_error(exc)
            result["failed"] += 1
            result["errors"].append(
                {
                    "row": index,
                    "external_id": order.external_id,
                    "status_code": mapped.status_code,
                    "reason": mapped.detail,
                    "retry_after": mapped.headers.get("Retry-After") if mapped.headers else None,
                }
            )
            continue
        counter = "replayed" if recorded["replayed"] else "created"
        result[counter] += 1
        result["items"].append(recorded)
    return result


async def _record_adjustment(
    *,
    external_order_id: str,
    event_type: str,
    source_system: str,
    amount: Decimal | None,
    currency: str,
    reason: str,
    occurred_at: datetime,
    idempotency_key: uuid.UUID,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    auth_session_id: uuid.UUID,
) -> dict:
    await enforce_order_mutation_rate_limit(tenant_id, account_id, source_system)
    provenance_digest = authenticated_import_provenance_digest(tenant_id, source_system, auth_session_id)
    payload = {
        "source_system": source_system,
        "external_order_id": external_order_id,
        "event_type": event_type,
        "amount": amount,
        "currency": currency,
        "reason": reason,
        "occurred_at": occurred_at,
    }
    try:
        return await record_external_order_value_event(
            db,
            tenant_id=tenant_id,
            source_system=source_system,
            external_order_id=external_order_id,
            event_type=event_type,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
            payload_digest=canonical_order_payload_digest(payload),
            provenance_digest=provenance_digest,
            auth_session_id=auth_session_id,
            occurred_at=occurred_at,
            reason=reason,
        )
    except DBAPIError as exc:
        raise _authority_http_error(exc) from exc


@gmv_router.post(
    "/orders/{external_order_id}/refund",
    summary="记录订单退款",
    dependencies=order_dependencies("order:manage"),
)
async def refund_order_endpoint(
    external_order_id: str,
    body: ExternalOrderRefundRequest,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    auth_session_id: uuid.UUID = Depends(require_direct_brand_order_principal),
):
    return await _record_adjustment(
        external_order_id=external_order_id,
        event_type="refund",
        source_system=body.source_system,
        amount=body.amount,
        currency=body.currency,
        reason=body.reason,
        occurred_at=body.occurred_at,
        idempotency_key=idempotency_key,
        db=db,
        tenant_id=tenant_id,
        account_id=account_id,
        auth_session_id=auth_session_id,
    )


@gmv_router.post(
    "/orders/{external_order_id}/cancel",
    summary="记录订单取消",
    dependencies=order_dependencies("order:manage"),
)
async def cancel_order_endpoint(
    external_order_id: str,
    body: ExternalOrderCancelRequest,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    auth_session_id: uuid.UUID = Depends(require_direct_brand_order_principal),
):
    return await _record_adjustment(
        external_order_id=external_order_id,
        event_type="cancel",
        source_system=body.source_system,
        amount=None,
        currency="CNY",
        reason=body.reason,
        occurred_at=body.occurred_at,
        idempotency_key=idempotency_key,
        db=db,
        tenant_id=tenant_id,
        account_id=account_id,
        auth_session_id=auth_session_id,
    )


@gmv_router.get("/orders", summary="订单列表", dependencies=order_dependencies("order:read"))
async def list_orders_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    matched: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    orders, total = await list_orders(db, tenant_id, page=page, page_size=page_size, matched=matched)
    return PaginatedResponse(
        items=[
            {
                "id": str(o.id),
                "external_id": o.external_id,
                "amount": o.amount,
                "product_name": o.product_name,
                "matched": o.matched,
                "channel": o.channel,
                "source_system": o.source_system,
                "order_time": o.order_time.isoformat() if o.order_time else None,
            }
            for o in orders
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@gmv_router.post(
    "/auto-attribution",
    summary="批量自动归因",
    dependencies=order_dependencies("order:manage"),
)
async def auto_attribution_endpoint(
    body: AutoAttributionRequest = AutoAttributionRequest(),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    auth_session_id: uuid.UUID = Depends(require_direct_brand_order_principal),
):
    return await batch_auto_attribution(
        db,
        tenant_id,
        auth_session_id,
        window_hours=body.window_hours,
        limit=body.limit,
    )


@gmv_router.post(
    "/attributions/confirm",
    summary="确认可信订单归因",
    dependencies=order_dependencies("order:manage"),
)
async def confirm_attribution_endpoint(
    body: ConfirmedAttributionRequest,
    idempotency_key: CanonicalIdempotencyKey,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _auth_session_id: uuid.UUID = Depends(require_direct_brand_order_principal),
):
    await enforce_order_mutation_rate_limit(tenant_id, account_id, "gmv-attribution")
    try:
        return await confirm_gmv_attribution(
            db,
            tenant_id=tenant_id,
            auth_session_id=_auth_session_id,
            order_id=body.external_order_id,
            consumer_id=body.consumer_id,
            scan_event_id=body.scan_event_id,
            scan_event_time=body.scan_time,
            window_hours=body.attribution_window_hours,
            idempotency_key=str(idempotency_key),
            payload_digest=canonical_order_payload_digest(body.canonical_payload()),
        )
    except DBAPIError as exc:
        raise _attribution_authority_http_error(exc) from exc


@gmv_router.get(
    "/dashboard",
    summary="GMV 归因看板",
    dependencies=order_dependencies("order:read"),
    response_model=GmvDashboardResponse,
)
async def dashboard_endpoint(
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    channel: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_gmv_dashboard(
        db,
        tenant_id,
        start_date=start_date,
        end_date=end_date,
        campaign_id=campaign_id,
        channel=channel,
    )


@gmv_router.get(
    "/roi",
    summary="ROI 报表",
    dependencies=order_dependencies("order:read"),
    response_model=list[GmvRoiItem],
)
async def roi_report_endpoint(
    campaign_id: uuid.UUID | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_roi_report(db, tenant_id, campaign_id=campaign_id, start_date=start_date, end_date=end_date)


@gmv_router.get("/attributions", summary="归因记录列表", dependencies=order_dependencies("order:read"))
async def list_attributions_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    match_type: str | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    attrs, total = await list_attributions(
        db,
        tenant_id,
        page=page,
        page_size=page_size,
        match_type=match_type,
        campaign_id=campaign_id,
    )
    return PaginatedResponse(
        items=[
            {
                "id": str(a.id),
                "external_order_id": str(a.external_order_id),
                "public_id": a.public_id,
                "code_item_id": str(a.code_item_id) if a.code_item_id else None,
                "campaign_id": str(a.campaign_id) if a.campaign_id else None,
                "consumer_id": str(a.consumer_id) if a.consumer_id else None,
                "amount": a.amount,
                "match_type": a.match_type,
                "scan_time": a.scan_time.isoformat() if a.scan_time else None,
                "confidence_score": a.confidence_score,
                "attribution_window_hours": a.attribution_window_hours,
            }
            for a in attrs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@gmv_router.post(
    "/aggregate-daily",
    summary="手动触发日统计聚合",
    dependencies=order_dependencies("order:manage"),
)
async def aggregate_daily_endpoint(
    target_date: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    count = await aggregate_daily_stats(db, tenant_id, target_date=target_date)
    return {"aggregated": count}
