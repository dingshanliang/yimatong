"""Admin, consumer, and signed commerce-product integration surfaces."""

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.consumers import VerifiedConsumerScanContext, _resolve_scan_context, verify_consumer_scan_request
from app.core.database import get_db, get_db_for_consumer
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.commerce_integration import (
    CommerceConnectionCreate,
    CommerceConnectionCreated,
    CommerceConnectionItem,
    CommerceCouponEligibilityRequest,
    CommerceCouponEligibilityResponse,
    CommerceCouponTransitionRequest,
    CommerceCouponTransitionResponse,
    CommerceEventReceipt,
    CommerceHandoffIssued,
    CommerceHandoffRedeemed,
    CommerceHandoffRedeemRequest,
    CommerceHandoffRequest,
    CommerceIncomingEvent,
    CommerceOrderFactList,
    CommerceProductMappingCreate,
    CommerceProductMappingItem,
    CommerceReconciliation,
    CommerceRepurchaseMetrics,
    CredentialRotateRequest,
    CredentialSecret,
)
from app.services.brand_membership import get_brand_membership_for_profile
from app.services.channel_access import require_brand_channel_principal
from app.services.commerce_coupon import list_eligible_commerce_coupons, transition_commerce_coupon
from app.services.commerce_integration import (
    _connection_dict,
    accept_commerce_event,
    commerce_reconciliation,
    commerce_repurchase_metrics,
    create_commerce_connection,
    create_commerce_product_mapping,
    decode_commerce_handoff,
    disconnect_commerce_connection,
    issue_commerce_handoff,
    list_commerce_connections,
    list_commerce_order_facts,
    redeem_commerce_handoff,
    resolve_commerce_credential,
    revoke_commerce_credential,
    rotate_commerce_credential,
    verify_commerce_signature,
)
from app.utils.auth_rbac import require_permission

commerce_integration_router = APIRouter(prefix="/api/v1", tags=["commerce-integration"])
_MANAGE = [Depends(require_brand_channel_principal), Depends(require_permission("webhook:manage"))]
_READ = [Depends(require_brand_channel_principal), Depends(require_permission("webhook:read"))]


def _durable_auth_session_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="durable auth session required") from exc


@commerce_integration_router.post(
    "/commerce/connections",
    response_model=CommerceConnectionCreated,
    status_code=201,
    dependencies=_MANAGE,
)
async def create_connection_endpoint(
    request: Request,
    body: CommerceConnectionCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    connection, credentials = await create_commerce_connection(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_durable_auth_session_id(request),
        **body.model_dump(mode="json"),
    )
    return CommerceConnectionCreated(
        **{
            key: value
            for key, value in _connection_dict(connection).items()
            if key != "version" and key not in {"disconnected_at", "created_at", "updated_at"}
        },
        credentials=[CredentialSecret(**credential) for credential in credentials],
    )


@commerce_integration_router.get(
    "/commerce/connections", response_model=list[CommerceConnectionItem], dependencies=_READ
)
async def list_connections_endpoint(
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return [_connection_dict(item) for item in await list_commerce_connections(db, tenant_id)]


@commerce_integration_router.post(
    "/commerce/product-mappings",
    response_model=CommerceProductMappingItem,
    status_code=201,
    dependencies=_MANAGE,
)
async def create_product_mapping_endpoint(
    request: Request,
    body: CommerceProductMappingCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    return await create_commerce_product_mapping(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_durable_auth_session_id(request),
        **body.model_dump(),
    )


@commerce_integration_router.post(
    "/commerce/connections/{connection_id}/credentials/rotate",
    response_model=CredentialSecret,
    dependencies=_MANAGE,
)
async def rotate_credential_endpoint(
    request: Request,
    connection_id: uuid.UUID,
    body: CredentialRotateRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    return await rotate_commerce_credential(
        db,
        tenant_id=tenant_id,
        connection_id=connection_id,
        actor_id=actor_id,
        auth_session_id=_durable_auth_session_id(request),
        **body.model_dump(),
    )


@commerce_integration_router.delete(
    "/commerce/connections/{connection_id}/credentials/{credential_id}",
    status_code=204,
    dependencies=_MANAGE,
)
async def revoke_credential_endpoint(
    request: Request,
    connection_id: uuid.UUID,
    credential_id: uuid.UUID,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=120),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    await revoke_commerce_credential(
        db,
        tenant_id=tenant_id,
        connection_id=connection_id,
        credential_id=credential_id,
        idempotency_key=idempotency_key,
        actor_id=actor_id,
        auth_session_id=_durable_auth_session_id(request),
    )
    return Response(status_code=204)


@commerce_integration_router.delete(
    "/commerce/connections/{connection_id}", response_model=CommerceConnectionItem, dependencies=_MANAGE
)
async def disconnect_connection_endpoint(
    request: Request,
    connection_id: uuid.UUID,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=120),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    return _connection_dict(
        await disconnect_commerce_connection(
            db,
            tenant_id=tenant_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            auth_session_id=_durable_auth_session_id(request),
        )
    )


@commerce_integration_router.get(
    "/commerce/connections/{connection_id}/reconciliation",
    response_model=CommerceReconciliation,
    dependencies=_READ,
)
async def reconciliation_endpoint(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await commerce_reconciliation(db, tenant_id, connection_id)


@commerce_integration_router.post(
    "/consumers/membership/commerce-handoffs",
    response_model=CommerceHandoffIssued,
    status_code=201,
)
async def issue_handoff_endpoint(
    body: CommerceHandoffRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    membership = await get_brand_membership_for_profile(db, tenant_id, consumer_id)
    if membership is None:
        raise HTTPException(status_code=409, detail="active_membership_required")
    try:
        scan_event_id = uuid.UUID(str(scan_context.payload["scan_event_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="scan receipt required") from exc
    token, connection = await issue_commerce_handoff(
        db,
        tenant_id=tenant_id,
        connection_id=body.connection_id,
        membership_id=uuid.UUID(str(membership["membership_id"])),
        idempotency_key=body.idempotency_key,
        consumer_id=consumer_id,
        scan_event_id=scan_event_id,
        public_id=str(scan_context.payload["public_id"]),
    )
    return CommerceHandoffIssued(
        handoff_token=token,
        connection_id=connection.id,
        external_shop_ref=connection.external_shop_ref,
    )


@commerce_integration_router.post("/commerce/handoffs/redeem", response_model=CommerceHandoffRedeemed)
async def redeem_handoff_endpoint(
    body: CommerceHandoffRedeemRequest,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    token_payload = decode_commerce_handoff(body.handoff_token)
    connection, member_ref = await redeem_commerce_handoff(db, token=body.handoff_token, payload=token_payload)
    return CommerceHandoffRedeemed(
        connection_id=connection.id,
        external_tenant_ref=connection.external_tenant_ref,
        external_shop_ref=connection.external_shop_ref,
        member_ref=member_ref,
    )


@commerce_integration_router.post("/commerce/events", response_model=CommerceEventReceipt, status_code=202)
async def receive_commerce_event_endpoint(
    request: Request,
    body: CommerceIncomingEvent,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
    credential_id: uuid.UUID = Header(alias="X-Commerce-Credential-Id"),
    timestamp: str = Header(alias="X-Commerce-Timestamp", min_length=1, max_length=20),
    signature: str = Header(alias="X-Commerce-Signature", min_length=64, max_length=64),
):
    credential, connection = await resolve_commerce_credential(credential_id)
    raw_body = await request.body()
    verify_commerce_signature(
        credential=credential,
        timestamp=timestamp,
        path=request.url.path,
        body=raw_body,
        signature=signature,
    )
    parsed = body.model_dump(mode="json")
    message, replayed = await accept_commerce_event(
        db,
        credential=credential,
        connection=connection,
        event=parsed,
        body_digest=hashlib.sha256(raw_body).hexdigest(),
    )
    return CommerceEventReceipt(
        message_id=message.message_id,
        message_version=message.message_version,
        status="accepted",
        replayed=replayed,
    )


@commerce_integration_router.post(
    "/commerce/coupons/eligible", response_model=CommerceCouponEligibilityResponse
)
async def eligible_commerce_coupons_endpoint(
    request: Request,
    body: CommerceCouponEligibilityRequest,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
    credential_id: uuid.UUID = Header(alias="X-Commerce-Credential-Id"),
    timestamp: str = Header(alias="X-Commerce-Timestamp", min_length=1, max_length=20),
    signature: str = Header(alias="X-Commerce-Signature", min_length=64, max_length=64),
):
    credential, connection = await resolve_commerce_credential(credential_id)
    raw_body = await request.body()
    verify_commerce_signature(
        credential=credential,
        timestamp=timestamp,
        path=request.url.path,
        body=raw_body,
        signature=signature,
    )
    coupons = await list_eligible_commerce_coupons(
        db,
        credential=credential,
        connection=connection,
        member_ref=body.member_ref,
        goods_subtotal_fen=body.goods_subtotal_fen,
        line_items=[item.model_dump() for item in body.line_items],
    )
    return CommerceCouponEligibilityResponse(coupons=coupons)


@commerce_integration_router.post(
    "/commerce/coupons/transitions", response_model=CommerceCouponTransitionResponse
)
async def transition_commerce_coupon_endpoint(
    request: Request,
    body: CommerceCouponTransitionRequest,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
    credential_id: uuid.UUID = Header(alias="X-Commerce-Credential-Id"),
    timestamp: str = Header(alias="X-Commerce-Timestamp", min_length=1, max_length=20),
    signature: str = Header(alias="X-Commerce-Signature", min_length=64, max_length=64),
):
    credential, connection = await resolve_commerce_credential(credential_id)
    raw_body = await request.body()
    verify_commerce_signature(
        credential=credential,
        timestamp=timestamp,
        path=request.url.path,
        body=raw_body,
        signature=signature,
    )
    coupon = await transition_commerce_coupon(
        db,
        credential=credential,
        connection=connection,
        member_ref=body.member_ref,
        coupon_ref=body.coupon_ref,
        order_id=body.order_id,
        amount_fen=body.amount_fen,
        action=body.action,
        idempotency_key=body.idempotency_key,
        goods_subtotal_fen=body.goods_subtotal_fen,
        line_items=[item.model_dump() for item in body.line_items],
        full_refund=body.full_refund,
    )
    return CommerceCouponTransitionResponse(
        coupon_ref=coupon.id,
        status=coupon.status,
        discount_amount_fen=body.amount_fen,
        reservation_expires_at=coupon.reservation_expires_at,
    )


def canonical_commerce_event_body(event: CommerceIncomingEvent) -> bytes:
    """Stable helper for clients/tests that need the exact signed JSON bytes."""

    return json.dumps(event.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False).encode()


@commerce_integration_router.get("/commerce/orders", response_model=CommerceOrderFactList, dependencies=_READ)
async def list_order_facts_endpoint(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    items, total = await list_commerce_order_facts(db, tenant_id, page=page, page_size=page_size)
    return CommerceOrderFactList(items=items, total=total)


@commerce_integration_router.get(
    "/commerce/repurchase/metrics", response_model=CommerceRepurchaseMetrics, dependencies=_READ
)
async def repurchase_metrics_endpoint(
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await commerce_repurchase_metrics(db, tenant_id)
