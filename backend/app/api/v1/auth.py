import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_account_id, get_current_tenant, get_redis_cache
from app.models.tenant import Account
from app.schemas.common import UNAUTHORIZED_EXAMPLE, ErrorDetail
from app.services.auth import (
    AuthError,
    authenticate_login,
    confirm_password_reset,
    generate_password_reset,
    logout_session,
    refresh_access_token,
)
from app.services.redis_cache import AsyncRedisCache
from app.utils.security import (
    clear_auth_cookies,
    set_auth_cookies,
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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=255, description="登录邮箱", examples=["admin@example.com"])
    password: str = Field(..., min_length=6, description="密码", examples=["SecurePass123!"])
    tenant_slug: str | None = Field(None, max_length=100, description="租户标识，用于多租户同邮箱登录")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT 访问令牌")
    refresh_token: str = Field(..., description="JWT 刷新令牌")
    token_type: str = Field("bearer", description="令牌类型")
    expires_in: int = Field(900, description="access_token 有效时间（秒）")


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(None, description="刷新令牌")


class MeResponse(BaseModel):
    id: str = Field(..., description="账号 ID")
    email: str = Field(..., description="邮箱")
    name: str = Field(..., description="姓名")
    tenant_id: str = Field(..., description="租户 ID")
    organization_id: str | None = Field(None, description="组织 ID")
    role: str = Field(..., description="角色")
    tenant_type: str = Field("brand", description="租户类型")


class GenerateResetTokenRequest(BaseModel):
    account_id: str = Field(..., description="需要重置密码的账户 ID")


class GenerateResetTokenResponse(BaseModel):
    reset_token: str = Field(..., description="一次性重置令牌")
    reset_url: str = Field(..., description="完整的重置链接（拼好 base_url）")


class ConfirmResetPasswordRequest(BaseModel):
    token: str = Field(..., description="重置令牌")
    account_id: str = Field(..., description="账户 ID")
    new_password: str = Field(
        ...,
        min_length=8,
        description="新密码（8 位以上，必须包含字母和数字）",
    )


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        .split(",")[0]
        .strip()
    )


def _build_token_response(token_pair: dict) -> JSONResponse:
    response = JSONResponse(content=token_pair)
    set_auth_cookies(response, token_pair["access_token"], token_pair["refresh_token"])
    return response


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="账号登录",
    response_description="登录成功，返回 JWT 令牌",
)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    try:
        token_pair = await authenticate_login(
            db=db,
            email=body.email,
            password=body.password,
            tenant_slug=body.tenant_slug,
            client_ip=_client_ip(request),
            cache=cache,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail, headers=e.headers) from e
    return _build_token_response(token_pair)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="刷新 Token",
    response_description="刷新成功，返回新的 JWT 令牌",
)
async def refresh(
    request: Request,
    body: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_db),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token and body:
        refresh_token = body.refresh_token

    try:
        token_pair = await refresh_access_token(db=db, refresh_token=refresh_token, cache=cache)
    except AuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail) from e
    return _build_token_response(token_pair)


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
        raise HTTPException(status_code=404, detail="账户不存在")
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
async def logout(request: Request, cache: AsyncRedisCache = Depends(get_redis_cache)):
    """登出端点：将当前 access token 的 jti 加入黑名单"""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else request.cookies.get("access_token")

    refresh_token_str = request.cookies.get("refresh_token") or ""
    if not refresh_token_str:
        try:
            body = await request.json()
            refresh_token_str = body.get("refresh_token", "")
        except Exception:
            pass

    await logout_session(access_token=token, refresh_token_str=refresh_token_str or None, cache=cache)

    response = JSONResponse(content={"status": "ok"})
    clear_auth_cookies(response)
    return response


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
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    """管理员为指定账户生成一次性密码重置令牌，存入 Redis（1 小时有效）。"""
    try:
        result = await generate_password_reset(
            db=db, account_id_str=body.account_id, tenant_id=tenant_id, cache=cache
        )
    except AuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail) from e
    return GenerateResetTokenResponse(reset_token=result["reset_token"], reset_url=result["reset_url"])


@router.post(
    "/confirm-reset-password",
    summary="用户通过重置令牌设置新密码",
    responses={400: {"model": ErrorDetail}},
)
async def confirm_reset_password(
    body: ConfirmResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    """用户通过重置令牌自助设置新密码。令牌验证后立即失效。"""
    try:
        return await confirm_password_reset(
            db=db,
            token=body.token,
            account_id_str=body.account_id,
            new_password=body.new_password,
            client_ip=_client_ip(request),
            cache=cache,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail, headers=e.headers) from e


@router.get(
    "/reset-page",
    summary="重置密码页面（重定向到前端）",
)
async def reset_page_redirect(token: str, account_id: str):
    """将后端短链重定向到前端重置密码页面。"""
    frontend_url = settings.cors_origins.split(",")[0].strip()
    return RedirectResponse(f"{frontend_url}/reset-password?token={token}&account_id={account_id}")
