"""邀请码管理 API"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_with_bypass
from app.core.dependencies import get_current_account_id
from app.models.invite_code import InviteCodeStatus
from app.schemas.common import PaginatedResponse
from app.services.invite_code import (
    create_invite_code,
    list_invite_codes,
    register_tenant_with_invite,
    toggle_invite_code_status,
)
from app.utils.auth_rbac import require_role

router = APIRouter(prefix="/api/v1/invite-codes", tags=["invite-codes"])


class InviteCodeCreateRequest(BaseModel):
    tenant_type: str = Field(default="brand", description="租户类型")
    max_uses: int = Field(default=1, ge=1, le=1000, description="最大使用次数")
    expires_in_days: int | None = Field(default=30, ge=1, le=365, description="有效期（天）")


class InviteCodeResponse(BaseModel):
    id: uuid.UUID
    code: str
    tenant_type: str
    max_uses: int
    used_count: int
    status: str
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantRegisterRequest(BaseModel):
    invite_code: str = Field(..., min_length=6, max_length=50, description="邀请码")
    name: str = Field(..., min_length=1, max_length=100, description="租户名称")
    slug: str | None = Field(None, max_length=50, description="URL 标识")
    admin_email: EmailStr
    admin_name: str = Field(..., min_length=1, max_length=100)
    admin_password: str = Field(..., min_length=8, max_length=128)
    industry: str | None = Field(None, max_length=50)


class TenantRegisterResponse(BaseModel):
    tenant_id: uuid.UUID
    message: str


from datetime import datetime


# ---------------------------------------------------------------------------
# Platform admin endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=InviteCodeResponse, status_code=201)
async def generate_invite_code(
    body: InviteCodeCreateRequest,
    db: AsyncSession = Depends(get_db_with_bypass),
    account_id: uuid.UUID = Depends(get_current_account_id),
    _role: str = Depends(require_role("platform_admin")),
):
    """平台管理员生成邀请码"""
    invite = await create_invite_code(
        db=db,
        created_by=account_id,
        tenant_type=body.tenant_type,
        max_uses=body.max_uses,
        expires_in_days=body.expires_in_days,
    )
    return invite


@router.get("", response_model=PaginatedResponse)
async def list_invite_code_endpoint(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_with_bypass),
    _role: str = Depends(require_role("platform_admin")),
):
    """邀请码列表"""
    items, total = await list_invite_codes(
        db=db, status=status, page=page, page_size=page_size
    )
    return PaginatedResponse(
        items=[InviteCodeResponse.model_validate(i) for i in items],
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
    return invite


# ---------------------------------------------------------------------------
# Public registration endpoint
# ---------------------------------------------------------------------------


@router.post("/register", response_model=TenantRegisterResponse, status_code=201)
async def register_with_invite_code(
    body: TenantRegisterRequest,
    db: AsyncSession = Depends(get_db_with_bypass),
):
    """使用邀请码注册租户"""
    try:
        tenant = await register_tenant_with_invite(
            db=db,
            invite_code=body.invite_code,
            name=body.name,
            slug=body.slug,
            admin_email=body.admin_email,
            admin_name=body.admin_name,
            admin_password=body.admin_password,
            industry=body.industry,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return TenantRegisterResponse(
        tenant_id=tenant.id,
        message="注册成功，请等待平台审核",
    )
