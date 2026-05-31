import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_account_id
from app.models.tenant import Account
from app.schemas.common import UNAUTHORIZED_EXAMPLE, ErrorDetail
from app.services.redis_cache import AsyncRedisCache
from app.utils import utcnow
from app.utils.security import create_access_token, create_refresh_token, decode_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 认证相关通用响应
AUTH_RESPONSES = {
    401: {
        "model": ErrorDetail,
        "description": "认证失败",
        "content": {"application/json": {"example": UNAUTHORIZED_EXAMPLE}},
    },
}

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


def _resolve_account_role(account: Account) -> str:
    role_names = {role.name for role in account.roles}
    for role in ("platform_admin", "admin", "operator"):
        if role in role_names:
            return role
    return sorted(role_names)[0] if role_names else "admin"


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255, description="登录邮箱", examples=["admin@example.com"])
    password: str = Field(..., min_length=1, description="密码", examples=["SecurePass123!"])


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT 访问令牌")
    refresh_token: str = Field(..., description="JWT 刷新令牌")
    token_type: str = Field("bearer", description="令牌类型")
    expires_in: int = Field(900, description="access_token 有效时间（秒）")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="刷新令牌", examples=["eyJhbGciOiJIUzI1NiIs..."])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="账号登录",
    response_description="登录成功，返回 JWT 令牌",
)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).options(selectinload(Account.roles)).where(Account.email == body.email))
    account = result.scalar_one_or_none()

    now = utcnow()

    if not account or not verify_password(body.password, account.hashed_password):
        if account:
            account.failed_login_attempts += 1
            if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                account.locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
            await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if account.locked_until and account.locked_until > now:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    await db.commit()

    access = create_access_token(str(account.tenant_id), str(account.id), _resolve_account_role(account))
    refresh = create_refresh_token(str(account.id))
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="刷新 Token",
    response_description="刷新成功，返回新的 JWT 令牌",
)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    from app.utils.security import verify_refresh_token

    payload = verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    account_id = payload["sub"]
    result = await db.execute(
        select(Account).options(selectinload(Account.roles)).where(Account.id == uuid.UUID(account_id))
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=401, detail="Account not found")
    access = create_access_token(str(account.tenant_id), str(account.id), _resolve_account_role(account))
    refresh = create_refresh_token(str(account.id))
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


class MeResponse(BaseModel):
    id: str = Field(..., description="账号 ID")
    email: str = Field(..., description="邮箱")
    name: str = Field(..., description="姓名")
    tenant_id: str = Field(..., description="租户 ID")
    organization_id: str | None = Field(None, description="组织 ID")
    role: str = Field(..., description="角色")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="获取当前用户信息",
    response_description="当前登录账号的详细信息",
    responses={
        401: {
            "model": ErrorDetail,
            "description": "未认证或 Token 无效",
            "content": {"application/json": {"example": UNAUTHORIZED_EXAMPLE}},
        },
    },
)
async def me(
    request: Request,
    db: AsyncSession = Depends(get_db),
    account_id: uuid.UUID = Depends(get_current_account_id),
):
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return MeResponse(
        id=str(account.id),
        email=account.email,
        name=account.name,
        tenant_id=str(account.tenant_id),
        organization_id=str(account.organization_id) if account.organization_id else None,
        role=request.state.role if hasattr(request.state, "role") else "admin",
    )


@router.post(
    "/logout",
    summary="登出",
    response_description="登出成功，当前 access_token 加入黑名单",
)
async def logout(request: Request):
    """登出端点：将当前 access token 的 jti 加入黑名单"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return {"status": "ok"}

    try:
        payload = decode_token(auth_header[7:])
    except Exception:
        # Token malformed — already unusable, return ok to client
        return {"status": "ok"}

    jti = payload.get("jti")
    if not jti:
        return {"status": "ok"}

    exp = payload.get("exp")
    if exp:
        remaining = max(1, int(exp - datetime.now(UTC).timestamp()))
    else:
        remaining = settings.access_token_expire_minutes * 60

    cache = AsyncRedisCache()
    await cache.revoke_token(jti, ttl=remaining)
    return {"status": "ok"}
