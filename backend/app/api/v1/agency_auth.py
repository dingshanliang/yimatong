"""Agency 授权管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import (
    get_current_account_id,
    get_current_role,
    get_current_tenant,
    get_current_tenant_type,
)
from app.schemas.agency_auth import (
    AuthorizationCreate,
    AuthorizationListResponse,
    AuthorizationResponse,
)
from app.services.agency_auth import (
    authorize_agency,
    list_authorizations_for_agency,
    list_authorizations_for_brand,
    resolve_active_agency_by_slug,
    revoke_authorization,
    verify_authorization,
)
from app.services.audit import write_audit_log
from app.utils.auth_rbac import require_permission, require_role
from app.utils.security import set_auth_cookies

router = APIRouter(prefix="/api/v1/ops/authorizations", tags=["agency-auth"])


def _require_brand(tenant_type: str) -> None:
    if tenant_type != "brand":
        raise HTTPException(status_code=403, detail="仅品牌租户可操作")


@router.get("", response_model=AuthorizationListResponse)
async def list_authorizations(
    request: Request,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页数量"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    db: AsyncSession = Depends(get_db),
):
    """查看授权列表：agency 看自己的客户，brand 看哪些 agency 有权"""
    if tenant_type == "brand" and "tenant:manage" not in getattr(request.state, "permissions", []):
        raise HTTPException(status_code=403, detail="缺少权限: tenant:manage")
    if tenant_type == "agency" and getattr(request.state, "role", None) not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="当前角色不能查看代运营授权")
    if tenant_type == "agency":
        items, total = await list_authorizations_for_agency(db, tenant_id, page, page_size)
    else:
        items, total = await list_authorizations_for_brand(db, tenant_id, page, page_size)

    return AuthorizationListResponse(
        items=[AuthorizationResponse(**item) for item in items],
        total=total,
    )


@router.post("", response_model=AuthorizationResponse, status_code=status.HTTP_201_CREATED)
async def create_authorization(
    body: AuthorizationCreate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    account_id: uuid.UUID = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    """Brand 授权 agency"""
    _require_brand(tenant_type)

    try:
        agency_tenant_id = body.agency_tenant_id
        if agency_tenant_id is None and body.agency_slug:
            agency = await resolve_active_agency_by_slug(db, body.agency_slug)
            if agency is None:
                raise ValueError("未找到可授权的代运营服务商")
            agency_tenant_id = agency.id
        assert agency_tenant_id is not None
        auth = await authorize_agency(
            db,
            agency_tenant_id=agency_tenant_id,
            client_tenant_id=tenant_id,
            scope=body.scope,
            granted_by=account_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await db.flush()
    await db.refresh(auth)
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="agency_authorization_granted",
        resource=f"agency_authorization:{auth.id}",
        details={"agency_tenant_id": str(auth.agency_tenant_id), "scope": auth.scope},
    )
    return AuthorizationResponse(
        id=auth.id,
        agency_tenant_id=auth.agency_tenant_id,
        client_tenant_id=auth.client_tenant_id,
        scope=auth.scope,
        status=auth.status.value,
        granted_by=auth.granted_by,
        granted_at=auth.granted_at,
    )


@router.delete("/{auth_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_auth(
    auth_id: uuid.UUID,
    tenant_type: str = Depends(get_current_tenant_type),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    account_id: uuid.UUID = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
    _permission: None = Depends(require_permission("tenant:manage")),
):
    """Brand 撤销 agency 授权"""
    _require_brand(tenant_type)

    auth = await revoke_authorization(db, auth_id, client_tenant_id=tenant_id)
    if not auth:
        raise HTTPException(status_code=404, detail="授权记录不存在")
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(tenant_id),
        action="agency_authorization_revoked",
        resource=f"agency_authorization:{auth.id}",
        details={"agency_tenant_id": str(auth.agency_tenant_id), "scope": auth.scope},
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Context switching endpoints (agency -> brand)
# ---------------------------------------------------------------------------

_switch_router = APIRouter(prefix="/api/v1/agency", tags=["agency-context"])


def _require_agency(tenant_type: str) -> None:
    if tenant_type != "agency":
        raise HTTPException(status_code=403, detail="仅代运营租户可操作")


class SwitchContextRequest(BaseModel):
    client_tenant_id: uuid.UUID = Field(..., description="要切换到的品牌客户租户 ID")


class SwitchContextResponse(BaseModel):
    access_token: str = Field(..., description="包含 acting_tenant_id 的新 JWT")
    acting_tenant_id: str = Field(..., description="当前代理的品牌租户 ID")
    scope: list[str] = Field(default_factory=list, description="授权范围")


class ExitContextResponse(BaseModel):
    access_token: str = Field(..., description="恢复为原始 agency JWT")
    acting_tenant_id: None = Field(None, description="已退出代理上下文")


@_switch_router.post("/switch-context", response_model=SwitchContextResponse)
async def switch_context(
    body: SwitchContextRequest,
    request: Request,
    response: Response,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    account_id: uuid.UUID = Depends(get_current_account_id),
    role: str = Depends(require_role("admin", "operator")),
    db: AsyncSession = Depends(get_db),
):
    """Agency 切换到客户上下文，返回带 acting_tenant_id 的新 JWT"""
    _require_agency(tenant_type)

    # Verify authorization
    auth = await verify_authorization(db, tenant_id, body.client_tenant_id)
    if not auth:
        raise HTTPException(status_code=403, detail="未获得该客户的授权")

    # Generate new JWT with acting_tenant_id
    from app.utils.security import create_access_token

    access_token = create_access_token(
        tenant_id=str(tenant_id),
        account_id=str(account_id),
        role=role,
        tenant_type=tenant_type,
        extra={
            "acting_tenant_id": str(body.client_tenant_id),
            "scope": auth.scope,
            "auth_version": request.state.auth_version,
            "sid": request.state.session_id,
        },
    )
    await write_audit_log(
        db,
        operator_id=str(account_id),
        target_tenant_id=str(body.client_tenant_id),
        action="agency_context_entered",
        resource=f"agency_authorization:{auth.id}",
        details={
            "agency_tenant_id": str(tenant_id),
            "acting_tenant_id": str(body.client_tenant_id),
            "scope": list(auth.scope),
        },
    )
    await db.flush()
    # Keep the server-rendered navigation identity aligned with the Bearer
    # token used by API requests. JavaScript cannot replace an HttpOnly cookie.
    set_auth_cookies(response, access_token)
    return SwitchContextResponse(
        access_token=access_token,
        acting_tenant_id=str(body.client_tenant_id),
        scope=auth.scope,
    )


@_switch_router.post("/exit-context", response_model=ExitContextResponse)
async def exit_context(
    request: Request,
    response: Response,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    account_id: uuid.UUID = Depends(get_current_account_id),
    role: str = Depends(get_current_role),
    db: AsyncSession = Depends(get_db),
):
    """Agency 退出客户上下文，返回原始 JWT"""
    _require_agency(tenant_type)

    from app.utils.security import create_access_token

    original_tenant_id = getattr(request.state, "original_tenant_id", None)
    if not original_tenant_id:
        raise HTTPException(status_code=409, detail="当前未处于代运营客户上下文")
    access_token = create_access_token(
        tenant_id=str(original_tenant_id),
        account_id=str(account_id),
        role=role,
        tenant_type=tenant_type,
        extra={"auth_version": request.state.auth_version, "sid": request.state.session_id},
    )
    acting_tenant_id = getattr(request.state, "acting_tenant_id", None)
    await write_audit_log(
        db,
        operator_id=str(account_id),
        # Exit remains available after the client authorization is revoked or
        # expires, so record it in the original agency's own audit stream.
        target_tenant_id=str(original_tenant_id),
        action="agency_context_exited",
        resource=f"agency_context:{acting_tenant_id or original_tenant_id}",
        details={
            "agency_tenant_id": str(original_tenant_id),
            "acting_tenant_id": str(acting_tenant_id) if acting_tenant_id else None,
        },
    )
    await db.flush()
    set_auth_cookies(response, access_token)
    return ExitContextResponse(access_token=access_token, acting_tenant_id=None)
