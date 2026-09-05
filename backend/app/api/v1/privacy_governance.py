import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.api.v1.consumers import VerifiedConsumerScanContext, _resolve_scan_context, verify_consumer_scan_request
from app.core.database import get_db, get_db_for_consumer
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.schemas.privacy_governance import (
    ConsumerPrivacyRequestCreate,
    PiiRevealRequest,
    PrivacyRequestAction,
    SensitiveExportAction,
    SensitiveExportCreate,
    SensitiveExportDownload,
)
from app.services.brand_membership import get_brand_membership_for_profile
from app.services.channel_access import require_brand_channel_principal
from app.services.privacy_governance import (
    create_consumer_privacy_request,
    download_sensitive_export,
    list_privacy_requests,
    list_sensitive_exports,
    mutate_privacy_request,
    mutate_sensitive_export,
    prepare_sensitive_export,
    reveal_consumer_phone,
)
from app.utils.auth_rbac import require_permission

privacy_governance_router = APIRouter(prefix="/api/v1/privacy", tags=["privacy-governance"])
consumer_privacy_router = APIRouter(prefix="/api/v1/consumers/membership", tags=["privacy-governance"])
_MANAGE = [Depends(require_brand_channel_principal), Depends(require_permission("privacy:manage"))]


def _session_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="durable auth session required") from exc


@consumer_privacy_router.post("/privacy-requests", status_code=202)
async def consumer_create_privacy_request_endpoint(
    body: ConsumerPrivacyRequestCreate,
    scan_context: VerifiedConsumerScanContext = Depends(verify_consumer_scan_request),
    db: AsyncSession = Depends(get_db_for_consumer, scope="function"),
):
    tenant_id, consumer_id = await _resolve_scan_context(scan_context, db)
    membership = await get_brand_membership_for_profile(db, tenant_id, consumer_id)
    if not membership:
        raise HTTPException(status_code=403, detail="active_brand_membership_required")
    request_id = await create_consumer_privacy_request(
        db,
        tenant_id=tenant_id,
        consumer_id=consumer_id,
        membership_id=uuid.UUID(str(membership["membership_id"])),
        request_type=body.request_type,
        reason=body.reason,
        evidence={**body.evidence, "source": "consumer_member_center"},
    )
    return {"id": request_id, "status": "submitted", "response_sla_workdays": 15}


@privacy_governance_router.get("/rights-requests", dependencies=_MANAGE)
async def list_privacy_requests_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return {"items": await list_privacy_requests(db, tenant_id)}


@privacy_governance_router.patch("/rights-requests/{request_id}", dependencies=_MANAGE)
async def mutate_privacy_request_endpoint(
    request_id: uuid.UUID,
    request: Request,
    body: PrivacyRequestAction,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    await mutate_privacy_request(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_session_id(request),
        request_id=request_id,
        payload=body.model_dump(),
    )
    return {"id": request_id, "action": body.action, "accepted": True}


@privacy_governance_router.post(
    "/members/{consumer_id}/pii-reveal",
    dependencies=[Depends(require_brand_channel_principal), Depends(require_permission("consumer:pii_reveal"))],
)
async def reveal_member_pii_endpoint(
    consumer_id: uuid.UUID,
    request: Request,
    body: PiiRevealRequest,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    phone = await reveal_consumer_phone(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_session_id(request),
        consumer_id=consumer_id,
        reason=body.reason,
        ticket_ref=body.ticket_ref,
        request_trace_id=request.headers.get("X-Request-Id"),
    )
    return {"consumer_id": consumer_id, "phone": phone}


@privacy_governance_router.get("/sensitive-exports", dependencies=_MANAGE)
async def list_sensitive_exports_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return {"items": await list_sensitive_exports(db, tenant_id)}


@privacy_governance_router.post(
    "/sensitive-exports",
    status_code=202,
    dependencies=[Depends(require_brand_channel_principal), Depends(require_permission("export:sensitive_request"))],
)
async def request_sensitive_export_endpoint(
    request: Request,
    body: SensitiveExportCreate,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    export_id = uuid7()
    result = await mutate_sensitive_export(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_session_id(request),
        export_id=export_id,
        payload={"action": "request", **body.model_dump()},
    )
    return {"id": result["export_id"], "status": result["status"]}


@privacy_governance_router.post(
    "/sensitive-exports/{export_id}/approve",
    dependencies=[Depends(require_brand_channel_principal), Depends(require_permission("export:sensitive_approve"))],
)
async def approve_sensitive_export_endpoint(
    export_id: uuid.UUID,
    request: Request,
    body: SensitiveExportAction,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    result = await mutate_sensitive_export(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_session_id(request),
        export_id=export_id,
        payload={"action": "approve", "reason": body.reason},
    )
    return {"id": result["export_id"], "status": result["status"]}


@privacy_governance_router.post(
    "/sensitive-exports/{export_id}/prepare",
    dependencies=[Depends(require_brand_channel_principal), Depends(require_permission("export:sensitive_request"))],
)
async def prepare_sensitive_export_endpoint(
    export_id: uuid.UUID,
    request: Request,
    body: SensitiveExportAction,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    token = await prepare_sensitive_export(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_session_id(request),
        export_id=export_id,
        reason=body.reason,
    )
    return {"id": export_id, "status": "prepared", "download_token": token, "expires_in_hours": 24}


@privacy_governance_router.post(
    "/sensitive-exports/{export_id}/download",
    dependencies=[Depends(require_brand_channel_principal), Depends(require_permission("export:sensitive_request"))],
)
async def download_sensitive_export_endpoint(
    export_id: uuid.UUID,
    request: Request,
    body: SensitiveExportDownload,
    db: AsyncSession = Depends(get_db, scope="function"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    actor_id: uuid.UUID = Depends(get_current_account_id),
):
    content, file_name = await download_sensitive_export(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        auth_session_id=_session_id(request),
        export_id=export_id,
        token=body.download_token,
        reason=body.reason,
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"', "Cache-Control": "no-store"},
    )
