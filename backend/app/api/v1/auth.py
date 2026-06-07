import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant
from app.models.tenant import Account, Tenant
from app.schemas.common import UNAUTHORIZED_EXAMPLE, ErrorDetail
from app.services.redis_cache import AsyncRedisCache
from app.services.tenant import get_tenant
from app.utils import utcnow
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

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
    tenant_slug: str | None = Field(None, max_length=100, description="租户标识，用于多租户同邮箱登录")


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
    query = select(Account).options(selectinload(Account.roles)).where(Account.email == body.email)
    if body.tenant_slug:
        query = query.join(Tenant, Tenant.id == Account.tenant_id).where(Tenant.slug == body.tenant_slug)
    result = await db.execute(query)
    account = result.scalars().first()

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

    # Resolve tenant_type for JWT payload
    tenant = await get_tenant(db, account.tenant_id)
    tenant_type = tenant.tenant_type.value if tenant else "brand"

    access = create_access_token(
        str(account.tenant_id), str(account.id), _resolve_account_role(account), tenant_type
    )
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

    payload = await verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # 将旧 refresh token 加入黑名单（轮换）
    old_jti = payload.get("jti")
    if old_jti:
        old_exp = payload.get("exp")
        if old_exp:
            remaining = max(1, int(old_exp - datetime.now(UTC).timestamp()))
        else:
            remaining = settings.refresh_token_expire_days * 86400
        cache = AsyncRedisCache()
        await cache.revoke_token(old_jti, ttl=remaining)

    account_id = payload["sub"]
    result = await db.execute(
        select(Account).options(selectinload(Account.roles)).where(Account.id == uuid.UUID(account_id))
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=401, detail="Account not found")

    # Resolve tenant_type for JWT payload
    tenant = await get_tenant(db, account.tenant_id)
    tenant_type = tenant.tenant_type.value if tenant else "brand"

    access = create_access_token(
        str(account.tenant_id), str(account.id), _resolve_account_role(account), tenant_type
    )
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
    tenant_type: str = Field("brand", description="租户类型")


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
        tenant_type=getattr(request.state, "tenant_type", "brand"),
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

    # 同时撤销关联的 refresh token
    refresh_token_str = request.cookies.get("refresh_token") or ""
    if not refresh_token_str:
        # Try request body as fallback (legacy clients)
        try:
            body = await request.json()
            refresh_token_str = body.get("refresh_token", "")
        except Exception:
            pass
    if refresh_token_str:
        try:
            refresh_payload = decode_token(refresh_token_str)
            refresh_jti = refresh_payload.get("jti")
            if refresh_jti:
                refresh_exp = refresh_payload.get("exp")
                refresh_remaining = (
                    max(1, int(refresh_exp - datetime.now(UTC).timestamp()))
                    if refresh_exp
                    else settings.refresh_token_expire_days * 86400
                )
                await cache.revoke_token(refresh_jti, ttl=refresh_remaining)
        except Exception:
            pass  # refresh token invalid or malformed, ignore

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 密码重置（管理员生成一次性重置链接）
# ---------------------------------------------------------------------------

RESET_TOKEN_TTL = 3600  # 1 小时
RESET_TOKEN_KEY_PREFIX = "reset"


class GenerateResetTokenRequest(BaseModel):
    account_id: str = Field(..., description="需要重置密码的账户 ID")


class GenerateResetTokenResponse(BaseModel):
    reset_token: str = Field(..., description="一次性重置令牌")
    reset_url: str = Field(..., description="完整的重置链接（拼好 base_url）")


@router.post(
    "/generate-reset-token",
    response_model=GenerateResetTokenResponse,
    summary="管理员生成密码重置令牌",
    responses={**AUTH_RESPONSES, 404: {"model": ErrorDetail}},
)
async def generate_reset_token(
    body: GenerateResetTokenRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """管理员为指定账户生成一次性密码重置令牌，存入 Redis（1 小时有效）。"""
    try:
        account_uuid = uuid.UUID(body.account_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid account_id")

    account = await db.execute(
        select(Account).where(Account.id == account_uuid, Account.tenant_id == tenant_id)
    )
    if not account.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Account not found")

    token = secrets.token_urlsafe(32)
    cache = AsyncRedisCache()
    await cache.set(
        f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}",
        {"token": token, "account_id": str(account_uuid)},
        ttl=RESET_TOKEN_TTL,
    )
    reset_url = f"{settings.base_url}/api/v1/auth/reset-page?token={token}&account_id={account_uuid}"
    return GenerateResetTokenResponse(reset_token=token, reset_url=reset_url)


class ConfirmResetPasswordRequest(BaseModel):
    token: str = Field(..., description="重置令牌")
    account_id: str = Field(..., description="账户 ID")
    new_password: str = Field(
        ...,
        min_length=8,
        description="新密码（8 位以上，必须包含字母和数字）",
    )


@router.post(
    "/confirm-reset-password",
    summary="用户通过重置令牌设置新密码",
    responses={400: {"model": ErrorDetail}},
)
async def confirm_reset_password(
    body: ConfirmResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """用户通过重置令牌自助设置新密码。令牌验证后立即失效。"""
    # 验证密码强度：必须包含字母和数字
    has_letter = any(c.isalpha() for c in body.new_password)
    has_digit = any(c.isdigit() for c in body.new_password)
    if not (has_letter and has_digit):
        raise HTTPException(status_code=400, detail="密码必须包含字母和数字")

    # 从 Redis 取出 token 记录
    try:
        account_uuid = uuid.UUID(body.account_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid account_id")

    cache = AsyncRedisCache()
    record = await cache.get(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}")
    if not record:
        raise HTTPException(status_code=400, detail="重置链接已过期或不存在，请联系管理员重新生成")

    if record.get("token") != body.token or record.get("account_id") != body.account_id:
        raise HTTPException(status_code=400, detail="重置令牌无效")

    # 查找账户
    result = await db.execute(select(Account).where(Account.id == account_uuid))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # 更新密码
    account.hashed_password = hash_password(body.new_password)
    account.failed_login_attempts = 0
    account.locked_until = None
    await db.commit()

    # 立即删除 token（一次性）
    await cache.invalidate(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}")

    return {"status": "ok", "message": "密码已重置，请使用新密码登录"}


@router.get(
    "/reset-page",
    summary="重置密码页面（重定向到前端）",
)
async def reset_page_redirect(token: str, account_id: str):
    """将后端短链重定向到前端重置密码页面。"""
    from fastapi.responses import RedirectResponse

    # 从 base_url 推导前端地址（简单处理：用 cors_origins 的第一个）
    frontend_url = settings.cors_origins.split(",")[0].strip()
    return RedirectResponse(f"{frontend_url}/reset-password?token={token}&account_id={account_id}")
