"""Agency 授权管理 API"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
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
    revoke_authorization,
    verify_authorization,
)

router = APIRouter(prefix="/api/v1/ops/authorizations", tags=["agency-auth"])


def _require_brand(tenant_type: str) -> None:
    if tenant_type != "brand":
        raise HTTPException(status_code=403, detail="仅品牌租户可操作")


@router.get("", response_model=AuthorizationListResponse)
async def list_authorizations(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    db: AsyncSession = Depends(get_db),
):
    """查看授权列表：agency 看自己的客户，brand 看哪些 agency 有权"""
    if tenant_type == "agency":
        items = await list_authorizations_for_agency(db, tenant_id)
    else:
        items = await list_authorizations_for_brand(db, tenant_id)

    return AuthorizationListResponse(
        items=[AuthorizationResponse(**item) for item in items],
        total=len(items),
    )


@router.post("", response_model=AuthorizationResponse, status_code=status.HTTP_201_CREATED)
async def create_authorization(
    body: AuthorizationCreate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    account_id: uuid.UUID = Depends(get_current_account_id),
    db: AsyncSession = Depends(get_db),
):
    """Brand 授权 agency"""
    _require_brand(tenant_type)

    auth = await authorize_agency(
        db,
        agency_tenant_id=body.agency_tenant_id,
        client_tenant_id=tenant_id,
        scope=body.scope,
        granted_by=account_id,
    )
    await db.flush()
    await db.refresh(auth)
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
    db: AsyncSession = Depends(get_db),
):
    """Brand 撤销 agency 授权"""
    _require_brand(tenant_type)

    auth = await revoke_authorization(db, auth_id, client_tenant_id=tenant_id)
    if not auth:
        raise HTTPException(status_code=404, detail="授权记录不存在")
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
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    account_id: uuid.UUID = Depends(get_current_account_id),
    role: str = Depends(get_current_role),
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
        },
    )
    return SwitchContextResponse(
        access_token=access_token,
        acting_tenant_id=str(body.client_tenant_id),
        scope=auth.scope,
    )


@_switch_router.post("/exit-context", response_model=ExitContextResponse)
async def exit_context(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    tenant_type: str = Depends(get_current_tenant_type),
    account_id: uuid.UUID = Depends(get_current_account_id),
    role: str = Depends(get_current_role),
):
    """Agency 退出客户上下文，返回原始 JWT"""
    _require_agency(tenant_type)

    from app.utils.security import create_access_token

    access_token = create_access_token(
        tenant_id=str(tenant_id),
        account_id=str(account_id),
        role=role,
        tenant_type=tenant_type,
    )
    return ExitContextResponse(access_token=access_token, acting_tenant_id=None)
