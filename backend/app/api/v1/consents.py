"""Consumer consent policy and durable receipt endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_consumer_tenant_id
from app.core.database import get_db_for_consumer, set_session_tenant_context
from app.schemas.consent import ConsentGrantRequest, ConsentWithdrawRequest
from app.services.consent import (
    get_consumer_consent_receipt_status,
    get_current_consumer_policy,
    grant_consumer_consent_authority,
    require_consumer_scan_authority,
    withdraw_consumer_consent_authority,
)
from app.services.consumer_admission import enforce_public_consumer_admission
from app.services.scan_token import verify_scan_token
from app.services.visitor import link_visitor_to_consumer
from app.utils.client_ip import compute_ip_hash, get_client_ip

consent_router = APIRouter(tags=["consents"])


def _scan_authority(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="scan_token required")
    ip_hash = compute_ip_hash(get_client_ip(request))
    payload = verify_scan_token(auth_header[7:], expected_ip_hash=ip_hash)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid_scan_token")
    return require_consumer_scan_authority(payload), ip_hash


async def _bind_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    await set_session_tenant_context(db, tenant_id)
    set_consumer_tenant_id(str(tenant_id))


@consent_router.get("/api/v1/public/consents/policy")
async def current_consent_policy(
    request: Request,
    purpose: str = Query(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]{0,99}$"),
    db: AsyncSession = Depends(get_db_for_consumer),
):
    authority, _ = _scan_authority(request)
    await _bind_tenant(db, authority.tenant_id)
    return await get_current_consumer_policy(db, authority.tenant_id, purpose)


@consent_router.get("/api/v1/public/consents/{consent_id}/status")
async def consent_receipt_status(
    consent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_for_consumer),
):
    authority, _ = _scan_authority(request)
    await _bind_tenant(db, authority.tenant_id)
    return await get_consumer_consent_receipt_status(
        db,
        tenant_id=authority.tenant_id,
        consent_id=consent_id,
        scan_event_id=authority.scan_event_id,
        scan_time=authority.scan_time,
        public_id=authority.public_id,
        visitor_id=authority.visitor_id,
        token_consumer_id=authority.consumer_id,
    )


@consent_router.post("/api/v1/public/consents", status_code=201)
async def create_consent(
    request: Request,
    body: ConsentGrantRequest,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    authority, ip_hash = _scan_authority(request)
    await enforce_public_consumer_admission(get_client_ip(request), authority.rate_subject)
    await _bind_tenant(db, authority.tenant_id)
    if authority.consumer_id is not None and not await link_visitor_to_consumer(
        db,
        authority.tenant_id,
        authority.visitor_id,
        authority.consumer_id,
    ):
        raise HTTPException(status_code=403, detail="consumer_visitor_subject_denied")
    return await grant_consumer_consent_authority(
        db,
        tenant_id=authority.tenant_id,
        purpose=body.purpose,
        expected_version=body.policy_version,
        expected_digest=body.policy_digest,
        scan_event_id=authority.scan_event_id,
        scan_time=authority.scan_time,
        public_id=authority.public_id,
        visitor_id=authority.visitor_id,
        token_consumer_id=authority.consumer_id,
        ip_hash=ip_hash,
        user_agent=request.headers.get("user-agent", ""),
        idempotency_key=body.idempotency_key,
    )


@consent_router.post("/api/v1/public/consents/{consent_id}/withdraw")
async def withdraw_consent_endpoint(
    consent_id: uuid.UUID,
    request: Request,
    body: ConsentWithdrawRequest,
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    authority, _ = _scan_authority(request)
    await enforce_public_consumer_admission(get_client_ip(request), authority.rate_subject)
    await _bind_tenant(db, authority.tenant_id)
    return await withdraw_consumer_consent_authority(
        db,
        tenant_id=authority.tenant_id,
        consent_id=consent_id,
        scan_event_id=authority.scan_event_id,
        scan_time=authority.scan_time,
        public_id=authority.public_id,
        visitor_id=authority.visitor_id,
        token_consumer_id=authority.consumer_id,
        idempotency_key=body.idempotency_key,
    )
