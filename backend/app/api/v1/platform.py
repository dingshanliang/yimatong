import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.audit import query_audit_logs
from app.utils.rbac import require_role
from app.utils.security import create_access_token

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])

PLATFORM_ADMIN_EMAIL = "platform@yimatong.cn"
PLATFORM_ADMIN_PASSWORD_HASH = "$2b$12$LJ3tFv2Jq1GqD8K6mNzHROGvXeYwQbF5uITJz4sKCiVdA8pR2Hx3e"


class PlatformLoginRequest(BaseModel):
    email: str
    password: str


class PlatformTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuditLogRead(BaseModel):
    id: uuid.UUID
    operator_id: str
    target_tenant_id: str
    action: str
    resource: str
    timestamp: datetime

    model_config = {"from_attributes": True}


@router.post("/auth/login", response_model=PlatformTokenResponse)
async def platform_login(body: PlatformLoginRequest):
    """平台管理员独立认证路径"""
    # TODO: replace with proper platform admin credential check
    if body.email != PLATFORM_ADMIN_EMAIL or body.password != "platform_admin_2026":
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(
        tenant_id="platform",
        account_id="platform-admin",
        role="platform_admin",
    )
    return PlatformTokenResponse(access_token=token)


@router.get("/audit-logs", response_model=list[AuditLogRead])
async def list_audit_logs(
    db: AsyncSession = Depends(get_db),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    _role: str = Depends(require_role("platform_admin")),
):
    """查询审计记录（支持时间范围过滤）"""
    return await query_audit_logs(db, start_time=start_time, end_time=end_time)
