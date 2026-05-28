"""消费者同意记录端点（公开，H5 使用）"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.consent import grant_consent, withdraw_consent
from app.services.scan_token import verify_scan_token

consent_router = APIRouter(tags=["consents"])


class ConsentRequest(BaseModel):
    consent_type: str
    public_id: str | None = None


@consent_router.post("/api/v1/public/consents", status_code=201)
async def create_consent(
    request: Request,
    body: ConsentRequest,
    db: AsyncSession = Depends(get_db),
):
    """消费者授予同意（隐私政策、营销等）"""
    auth_header = request.headers.get("Authorization", "")
    tenant_id = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = verify_scan_token(token, body.public_id)
        if payload:
            tenant_id = payload.get("tenant_id")

    if not tenant_id:
        from app.services.resolver import resolve_public_code
        if body.public_id:
            data = await resolve_public_code(db, body.public_id)
            if data:
                tenant_id = data.get("tenant_id")

    if not tenant_id:
        return {"status": "ignored", "reason": "cannot_determine_tenant"}

    client_ip = request.client.host if request.client else None
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip else None

    record = await grant_consent(
        db=db,
        tenant_id=uuid.UUID(tenant_id),
        consent_type=body.consent_type,
        public_id=body.public_id,
        ip_hash=ip_hash,
    )
    return {
        "id": str(record.id),
        "consent_type": record.consent_type,
        "status": record.status,
        "granted_at": record.granted_at.isoformat(),
    }


@consent_router.post("/api/v1/public/consents/{consent_id}/withdraw")
async def withdraw_consent_endpoint(
    consent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """消费者撤回同意"""
    auth_header = request.headers.get("Authorization", "")
    tenant_id = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        from app.services.scan_token import verify_scan_token
        payload = verify_scan_token(token)
        if payload:
            tenant_id = payload.get("tenant_id")

    if not tenant_id:
        return {"status": "ignored", "reason": "no_tenant_context"}

    record = await withdraw_consent(db, uuid.UUID(tenant_id), consent_id)
    if not record:
        return {"status": "ignored", "reason": "not_found"}
    return {
        "id": str(record.id),
        "status": record.status,
        "withdrawn_at": record.withdrawn_at.isoformat() if record.withdrawn_at else None,
    }
