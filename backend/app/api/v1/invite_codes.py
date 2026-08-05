"""邀请码管理 API"""

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_with_bypass
from app.core.dependencies import get_redis_cache
from app.models.invite_code import TenantInviteCode
from app.models.invite_registration import InviteRegistrationReceipt
from app.schemas.common import PaginatedResponse
from app.schemas.invite_code import (
    InviteCodeCreateRequest,
    InviteCodeResponse,
    TenantRegisterRequest,
    TenantRegisterResponse,
)
from app.services.audit import write_audit_log
from app.services.invite_code import (
    IdempotencyConflictError,
    TenantRegistrationResult,
    _canonical_registration_hash,
    _keyed_digest,
    create_invite_code,
    list_invite_codes,
    register_tenant_with_invite,
    toggle_invite_code_status,
)
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.auth_rbac import require_role

router = APIRouter(prefix="/api/v1/invite-codes", tags=["invite-codes"])

_REGISTER_RATE_WINDOW_SECONDS = 300
_REGISTER_IP_MAX_ATTEMPTS = 10
_REGISTER_INVITE_MAX_ATTEMPTS = 5


def _invite_code_response(invite: TenantInviteCode) -> InviteCodeResponse:
    return InviteCodeResponse(
        id=invite.id,
        code=invite.code,
        tenant_type=invite.tenant_type,
        max_uses=invite.max_uses,
        used_count=invite.used_count,
        status=invite.status.value,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        registration_url=settings.build_admin_url(
            "/register",
            {"invite_code": invite.code},
        ),
    )


# ---------------------------------------------------------------------------
# Platform admin endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=InviteCodeResponse, status_code=201)
async def generate_invite_code(
    body: InviteCodeCreateRequest,
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """平台管理员生成邀请码"""
    invite = await create_invite_code(
        db=db,
        created_by_actor="platform-admin",
        tenant_type=body.tenant_type,
        max_uses=body.max_uses,
        expires_in_days=body.expires_in_days,
    )
    await write_audit_log(
        db,
        "platform-admin",
        "platform",
        "invite_code_created",
        f"invite_code:{invite.id}",
        {"tenant_type": invite.tenant_type, "max_uses": invite.max_uses},
    )
    return _invite_code_response(invite)


@router.get("", response_model=PaginatedResponse)
async def list_invite_code_endpoint(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """邀请码列表"""
    items, total = await list_invite_codes(db=db, status=status, page=page, page_size=page_size)
    return PaginatedResponse(
        items=[_invite_code_response(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/{code_id}/status", response_model=InviteCodeResponse)
async def update_invite_code_status(
    code_id: uuid.UUID,
    active: bool = Query(..., description="是否激活"),
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """激活/停用邀请码"""
    invite = await toggle_invite_code_status(db, code_id, active)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite code not found or cannot be changed")
    await write_audit_log(
        db,
        "platform-admin",
        "platform",
        "invite_code_status_changed",
        f"invite_code:{invite.id}",
        {"active": active, "status": invite.status.value},
    )
    return _invite_code_response(invite)


# ---------------------------------------------------------------------------
# Public registration endpoint
# ---------------------------------------------------------------------------


async def _find_completed_registration_replay(
    db: AsyncSession,
    *,
    idempotency_key: str,
    body: TenantRegisterRequest,
) -> TenantRegistrationResult | None:
    """Return only an exact, completed replay without entering initialization."""
    key_hash = _keyed_digest(idempotency_key.encode())
    request_hash = _canonical_registration_hash(
        invite_code=body.invite_code,
        name=body.name,
        admin_email=body.admin_email,
        admin_name=body.admin_name,
        admin_password=body.admin_password,
        industry=body.industry,
    )
    receipt = (
        await db.execute(
            select(InviteRegistrationReceipt).where(InviteRegistrationReceipt.idempotency_key_hash == key_hash)
        )
    ).scalar_one_or_none()
    if (
        receipt is None
        or not hmac.compare_digest(receipt.request_hash, request_hash)
        or receipt.tenant_id is None
        or receipt.tenant_slug is None
    ):
        return None
    return TenantRegistrationResult(tenant_id=receipt.tenant_id, tenant_slug=receipt.tenant_slug)


@router.post("/register", response_model=TenantRegisterResponse, status_code=201)
async def register_with_invite_code(
    body: TenantRegisterRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=200),
    db: AsyncSession = Depends(get_db_with_bypass),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    """使用邀请码注册租户"""
    # Only request.client is trusted here. Proxy forwarding must be resolved by
    # the ASGI server's trusted-proxy configuration, never by caller headers.
    client_ip = request.client.host if request.client else "unknown"
    client_ip_digest = _keyed_digest(client_ip.encode())
    invite_digest = _keyed_digest(body.invite_code.strip().upper().encode())
    try:
        ip_allowed, _ = await cache.rate_limit_check_shared(
            f"invite_register:ip:{client_ip_digest}",
            max_attempts=_REGISTER_IP_MAX_ATTEMPTS,
            window_seconds=_REGISTER_RATE_WINDOW_SECONDS,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise HTTPException(status_code=503, detail="注册服务暂时不可用，请稍后重试") from exc
    if not ip_allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "注册尝试过于频繁，请稍后再试"},
            headers={"Retry-After": str(_REGISTER_RATE_WINDOW_SECONDS)},
        )

    # A completed exact replay is a cheap indexed read after the IP gate. It
    # bypasses the invite-code budget so a lost success response remains
    # recoverable even after that code is depleted or temporarily rate-limited.
    replay = await _find_completed_registration_replay(db, idempotency_key=idempotency_key, body=body)
    if replay is not None:
        result = replay
    else:
        try:
            invite_allowed, _ = await cache.rate_limit_check_shared(
                f"invite_register:invite:{invite_digest}",
                max_attempts=_REGISTER_INVITE_MAX_ATTEMPTS,
                window_seconds=_REGISTER_RATE_WINDOW_SECONDS,
            )
        except SharedSecurityCacheUnavailable as exc:
            raise HTTPException(status_code=503, detail="注册服务暂时不可用，请稍后重试") from exc
        if not invite_allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "注册尝试过于频繁，请稍后再试"},
                headers={"Retry-After": str(_REGISTER_RATE_WINDOW_SECONDS)},
            )

        try:
            result = await register_tenant_with_invite(
                db=db,
                idempotency_key=idempotency_key,
                invite_code=body.invite_code,
                name=body.name,
                slug=body.slug,
                admin_email=body.admin_email,
                admin_name=body.admin_name,
                admin_password=body.admin_password,
                industry=body.industry,
            )
        except IdempotencyConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return TenantRegisterResponse(
        tenant_id=result.tenant_id,
        tenant_slug=result.tenant_slug,
        message="注册成功，租户已开通",
    )
