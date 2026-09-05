"""消费者相关端点（H5 与共享会员小程序使用，scan_token 鉴权）"""

import hashlib
import json
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.context import set_consumer_tenant_id
from app.core.database import get_db_for_consumer, lock_active_tenant_context, set_session_tenant_context
from app.models.member import ConsumerProfile
from app.schemas.common import PaginatedResponse
from app.schemas.consent import LeadCaptureRequest
from app.schemas.member import (
    ExchangeRequest as PointsExchangeRequest,
)
from app.schemas.member import (
    MembershipJoinRequest,
    MembershipMergeRequest,
    MembershipRecoveryRequest,
    MiniProgramIdentityBindRequest,
    MiniProgramSessionRequest,
)
from app.services.brand_membership import (
    bind_verified_member_identity,
    get_brand_membership_for_profile,
    get_membership_by_verified_identity,
    issue_member_recovery_token,
    join_brand_membership,
    merge_brand_memberships,
    recover_brand_membership,
)
from app.services.consent import capture_consumer_lead_authority, require_consumer_scan_authority
from app.services.consumer_admission import enforce_public_consumer_admission
from app.services.member import get_consumer_profile, list_point_transactions
from app.services.point_shop import exchange_product, list_consumer_point_products
from app.services.resolver import resolve_public_code
from app.services.scan_token import bind_scan_token_consumer, verify_scan_token
from app.services.visitor import link_visitor_to_consumer
from app.services.wechat_miniprogram import exchange_miniprogram_code
from app.utils.client_ip import compute_ip_hash, get_client_ip
from app.utils.crypto import encrypt_consumer_phone, hash_phone


async def _require_consumer_business_plan(db: AsyncSession, tenant_id: uuid.UUID) -> JSONResponse | None:
    from app.services.entitlement import (
        PLAN_EXPIRED_CODE,
        PLAN_EXPIRED_DETAIL,
        TenantPlanExpiredError,
    )

    try:
        await lock_active_tenant_context(db, tenant_id)
    except TenantPlanExpiredError:
        return JSONResponse(
            status_code=403,
            content={"code": PLAN_EXPIRED_CODE, "detail": PLAN_EXPIRED_DETAIL},
        )
    return None


consumer_router = APIRouter(prefix="/api/v1/consumers", tags=["consumers"])


@dataclass(frozen=True)
class VerifiedConsumerScanContext:
    payload: dict
    client_ip: str
    ip_hash: str | None


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="scan_token required")
    return auth_header[7:]


async def verify_consumer_scan_request(request: Request) -> VerifiedConsumerScanContext:
    """Verify the middleware-bypassed credential before opening a DB session."""
    token = _extract_bearer_token(request)
    client_ip = get_client_ip(request)
    ip_hash = compute_ip_hash(client_ip)
    payload = verify_scan_token(token, expected_ip_hash=ip_hash)
    if not payload or not payload.get("public_id"):
        raise HTTPException(status_code=401, detail="invalid token")
    return VerifiedConsumerScanContext(payload=payload, client_ip=client_ip, ip_hash=ip_hash)


async def _resolve_scan_context(
    scan_context: VerifiedConsumerScanContext,
    db: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Resolve tenant and the required private consumer subject from scan_token."""
    payload = scan_context.payload

    # 优先从 token payload 获取 tenant_id（减少 DB 查询）
    tid = payload.get("tenant_id")
    tenant_uuid: uuid.UUID
    if tid:
        tenant_uuid = await set_session_tenant_context(db, tid)
    else:
        code_data = await resolve_public_code(db, payload["public_id"])
        if not code_data:
            raise HTTPException(status_code=404, detail="code not found")
        tenant_uuid = uuid.UUID(code_data["tenant_id"])

    set_consumer_tenant_id(str(tenant_uuid))

    cid_str = payload.get("consumer_id")
    if not cid_str:
        raise HTTPException(status_code=401, detail="consumer-bound scan_token required")
    try:
        bound_consumer_id = uuid.UUID(cid_str)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid consumer subject")
    return tenant_uuid, bound_consumer_id


def _verify_consumer_ownership(bound_consumer_id: uuid.UUID, requested_consumer_id: uuid.UUID) -> None:
    """Reject request identifiers that disagree with the credential subject."""
    if bound_consumer_id != requested_consumer_id:
        raise HTTPException(status_code=403, detail="consumer_id mismatch with token")


@consumer_router.post("/lead-capture", status_code=201)
async def lead_capture(
    request: Request,
    body: LeadCaptureRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    """Capture a lead through exact scan and consent database authority."""
    payload = scan_context.payload
    authority = require_consumer_scan_authority(payload)
    await enforce_public_consumer_admission(scan_context.client_ip, authority.rate_subject)
    await set_session_tenant_context(db, authority.tenant_id)
    set_consumer_tenant_id(str(authority.tenant_id))
    if expired_response := await _require_consumer_business_plan(db, authority.tenant_id):
        return expired_response
    requested_consumer_id = authority.consumer_id or uuid7()
    phone_ciphertext, phone_nonce, phone_key_id = encrypt_consumer_phone(
        authority.tenant_id,
        requested_consumer_id,
        body.phone,
    )
    result = await capture_consumer_lead_authority(
        db,
        tenant_id=authority.tenant_id,
        requested_consumer_id=requested_consumer_id,
        consent_id=body.consent_id,
        scan_event_id=authority.scan_event_id,
        scan_time=authority.scan_time,
        public_id=authority.public_id,
        visitor_id=authority.visitor_id,
        token_consumer_id=authority.consumer_id,
        phone_hash=hash_phone(body.phone),
        phone_ciphertext=phone_ciphertext,
        phone_nonce=phone_nonce,
        phone_key_id=phone_key_id,
        requested_name=body.name,
        requested_lead_extra={
            key: value for key, value in {"region": body.region, "intention": body.intention}.items() if value
        },
        idempotency_key=body.idempotency_key,
    )
    if result["outcome"] != "captured":
        return JSONResponse(status_code=409, content={"code": result["outcome"], "detail": "Lead was not captured"})
    consumer_id = uuid.UUID(str(result["consumer_id"]))
    bound_token = bind_scan_token_consumer(payload, consumer_id, scan_context.ip_hash)
    return {
        "status": "captured",
        "consumer_id": str(consumer_id),
        "scan_token": bound_token,
        "replayed": bool(result["replayed"]),
    }


@consumer_router.get("/me")
async def get_consumer_me(
    request: Request,
    consumer_id: str | None = None,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer),
):
    """查询当前消费者信息（积分、等级），必须结合 scan_token 与 consumer_id。"""
    tenant_id, bound_cid = await _resolve_scan_context(scan_context, db)
    if consumer_id:
        try:
            cid = uuid.UUID(consumer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid consumer_id")

        _verify_consumer_ownership(bound_cid, cid)
    else:
        cid = bound_cid

    result = await db.execute(
        select(ConsumerProfile).where(
            ConsumerProfile.id == cid,
            ConsumerProfile.tenant_id == tenant_id,
        )
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="consumer not found")

    membership = await get_brand_membership_for_profile(db, tenant_id, cid)
    return {
        "consumer_id": str(profile.id),
        "nickname": None if profile.lead_contact_suppressed else profile.nickname,
        "membership": membership,
    }


@consumer_router.post("/membership/join", status_code=201)
async def join_membership_endpoint(
    body: MembershipJoinRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    authority = require_consumer_scan_authority(scan_context.payload)
    await enforce_public_consumer_admission(scan_context.client_ip, authority.rate_subject)
    await set_session_tenant_context(db, authority.tenant_id)
    set_consumer_tenant_id(str(authority.tenant_id))
    if expired_response := await _require_consumer_business_plan(db, authority.tenant_id):
        return expired_response
    result = await join_brand_membership(
        db,
        tenant_id=authority.tenant_id,
        consent_id=body.consent_id,
        scan_event_id=authority.scan_event_id,
        scan_time=authority.scan_time,
        public_id=authority.public_id,
        visitor_id=authority.visitor_id,
        token_consumer_id=authority.consumer_id,
        idempotency_key=body.idempotency_key,
    )
    consumer_id = uuid.UUID(str(result["consumer_id"]))
    if not await link_visitor_to_consumer(db, authority.tenant_id, authority.visitor_id, consumer_id):
        raise HTTPException(status_code=403, detail="consumer_visitor_subject_denied")
    result["scan_token"] = bind_scan_token_consumer(scan_context.payload, consumer_id, scan_context.ip_hash)
    return result


@consumer_router.post("/membership/recover")
async def recover_membership_endpoint(
    body: MembershipRecoveryRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    return await recover_brand_membership(
        db,
        tenant_id=tenant_id,
        current_consumer_id=consumer_id,
        recovery_token=body.recovery_token,
        idempotency_key=body.idempotency_key,
    )


@consumer_router.post("/membership/miniprogram-session")
async def establish_miniprogram_session(
    body: MiniProgramSessionRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    """Restore a brand member after WeChat verifies the shared mini-program login code."""

    authority = require_consumer_scan_authority(scan_context.payload)
    await enforce_public_consumer_admission(scan_context.client_ip, authority.rate_subject)
    await set_session_tenant_context(db, authority.tenant_id)
    set_consumer_tenant_id(str(authority.tenant_id))
    if expired_response := await _require_consumer_business_plan(db, authority.tenant_id):
        return expired_response

    identity = await exchange_miniprogram_code(body.js_code)
    found = await get_membership_by_verified_identity(
        db,
        tenant_id=authority.tenant_id,
        credential_type="wechat_openid",
        issuer=identity.issuer,
        subject=identity.openid,
    )
    if found is None:
        return {"status": "unbound"}

    membership, credential = found
    consumer_id = authority.consumer_id or uuid7()
    profile = await db.scalar(
        select(ConsumerProfile).where(
            ConsumerProfile.tenant_id == authority.tenant_id,
            ConsumerProfile.id == consumer_id,
        )
    )
    if profile is None:
        profile = ConsumerProfile(id=consumer_id, tenant_id=authority.tenant_id)
        db.add(profile)
        await db.flush()

    current = await get_brand_membership_for_profile(db, authority.tenant_id, consumer_id)
    if current is not None and uuid.UUID(str(current["membership_id"])) != membership.id:
        raise HTTPException(status_code=409, detail="membership_merge_requires_two_verified_credentials")
    if current is None:
        recovery_token = issue_member_recovery_token(authority.tenant_id, membership.id, credential.id)
        current = await recover_brand_membership(
            db,
            tenant_id=authority.tenant_id,
            current_consumer_id=consumer_id,
            recovery_token=recovery_token,
            idempotency_key=f"mini-session-{uuid.uuid4()}",
        )
    if not await link_visitor_to_consumer(db, authority.tenant_id, authority.visitor_id, consumer_id):
        raise HTTPException(status_code=403, detail="consumer_visitor_subject_denied")
    return {
        "status": "recovered",
        "membership": current,
        "scan_token": bind_scan_token_consumer(scan_context.payload, consumer_id, scan_context.ip_hash),
    }


@consumer_router.post("/membership/miniprogram-bind", status_code=201)
async def bind_miniprogram_identity(
    body: MiniProgramIdentityBindRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    """Bind a verified shared mini-program identity to an explicitly joined member."""

    authority = require_consumer_scan_authority(scan_context.payload)
    await enforce_public_consumer_admission(scan_context.client_ip, authority.rate_subject)
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    membership = await get_brand_membership_for_profile(db, tenant_id, consumer_id)
    if membership is None:
        raise HTTPException(status_code=409, detail="membership_required")
    identity = await exchange_miniprogram_code(body.js_code)
    receipt_hash = hashlib.sha256(
        json.dumps(
            {"provider": "wechat", "issuer": identity.issuer, "openid": identity.openid},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    credential = await bind_verified_member_identity(
        db,
        tenant_id=tenant_id,
        membership_id=uuid.UUID(str(membership["membership_id"])),
        credential_type="wechat_openid",
        issuer=identity.issuer,
        subject=identity.openid,
        verification_receipt_hash=receipt_hash,
        idempotency_key=body.idempotency_key,
    )
    return {
        "status": "bound",
        "membership": membership,
        "recovery_token": issue_member_recovery_token(tenant_id, credential.membership_id, credential.id),
        "expires_in": 300,
    }


@consumer_router.post("/membership/merge")
async def merge_membership_endpoint(
    body: MembershipMergeRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    return await merge_brand_memberships(
        db,
        tenant_id=tenant_id,
        current_consumer_id=consumer_id,
        current_recovery_token=body.current_recovery_token,
        target_recovery_token=body.target_recovery_token,
        idempotency_key=body.idempotency_key,
    )


@consumer_router.get("/points/me")
async def get_consumer_points_me(
    request: Request,
    consumer_id: uuid.UUID | None = None,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(scan_context, db)
    if consumer_id is not None:
        _verify_consumer_ownership(bound_cid, consumer_id)

    profile = await get_consumer_profile(db, tenant_id, bound_cid)
    if not profile:
        raise HTTPException(status_code=404, detail="consumer not found")
    return profile


@consumer_router.get("/points/transactions")
async def list_consumer_points_transactions(
    request: Request,
    consumer_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(scan_context, db)
    _verify_consumer_ownership(bound_cid, consumer_id)

    txns, total = await list_point_transactions(db, tenant_id, bound_cid, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[
            {
                "id": str(t.id),
                "amount": t.amount,
                "balance_after": t.balance_after,
                "txn_type": t.txn_type,
                "reason": t.reason,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txns
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@consumer_router.get("/points/products")
async def list_consumer_points_products(
    request: Request,
    consumer_id: uuid.UUID,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(scan_context, db)
    _verify_consumer_ownership(bound_cid, consumer_id)

    try:
        return {"items": await list_consumer_point_products(db, tenant_id, bound_cid)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@consumer_router.post("/points/exchanges")
async def create_consumer_points_exchange(
    request: Request,
    body: PointsExchangeRequest,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer),
):
    tenant_id, bound_cid = await _resolve_scan_context(scan_context, db)
    _verify_consumer_ownership(bound_cid, body.consumer_id)
    if expired_response := await _require_consumer_business_plan(db, tenant_id):
        return expired_response

    try:
        return await exchange_product(db, tenant_id, bound_cid, body.product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
