import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, get_db_for_auth, get_db_with_bypass
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
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.utils.auth_rbac import require_permission
from app.utils.email import normalize_email
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

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT 访问令牌")
    refresh_token: str | None = Field(None, description="JWT 刷新令牌；浏览器 cookie-only 模式不返回")
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
    reset_url: str = Field(..., description="基于 canonical Admin 公网基址生成的完整重置链接")


class ConfirmResetPasswordRequest(BaseModel):
    token: str = Field(..., description="重置令牌")
    account_id: str = Field(..., description="账户 ID")
    new_password: str = Field(
        ...,
        min_length=8,
        description="新密码（8 位以上，必须包含字母和数字）",
    )


def _client_ip(request: Request) -> str:
    # Proxy headers are trustworthy only when the ASGI server is configured
    # with trusted proxy addresses; request.client already reflects that policy.
    return request.client.host if request.client else "unknown"


def _build_token_response(token_pair: dict, *, cookie_only: bool) -> JSONResponse:
    content = dict(token_pair)
    if cookie_only:
        content.pop("refresh_token", None)
    response = JSONResponse(content=content)
    set_auth_cookies(response, token_pair["access_token"], token_pair["refresh_token"])
    return response


def _cookie_only_delivery(request: Request) -> bool:
    return request.headers.get("X-Auth-Delivery", "").lower() == "cookie"


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
    db: AsyncSession = Depends(get_db_for_auth),
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
    return _build_token_response(token_pair, cookie_only=_cookie_only_delivery(request))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="刷新 Token",
    response_description="刷新成功，返回新的 JWT 令牌",
)
async def refresh(
    request: Request,
    body: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_db_for_auth),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    explicit_refresh_token = body.refresh_token if body and body.refresh_token else None
    refresh_token = explicit_refresh_token or request.cookies.get("refresh_token")

    try:
        token_pair = await refresh_access_token(db=db, refresh_token=refresh_token, cache=cache)
    except AuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail) from e
    # The server, not a caller-controlled header, determines whether this is a
    # browser-cookie rotation. A same-origin script must never turn an HttpOnly
    # refresh credential into a JSON-readable successor by omitting a header.
    return _build_token_response(
        token_pair,
        cookie_only=explicit_refresh_token is None or _cookie_only_delivery(request),
    )


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
async def logout(
    request: Request,
    body: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_db),
    cache: AsyncRedisCache = Depends(get_redis_cache),
):
    """登出端点：全局撤销当前 access/refresh 会话。"""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else request.cookies.get("access_token")

    explicit_refresh_token = body.refresh_token if body and body.refresh_token else None
    cookie_refresh_token = request.cookies.get("refresh_token")
    try:
        await logout_session(
            db=db,
            access_token=token,
            refresh_token_str=cookie_refresh_token or explicit_refresh_token,
            additional_refresh_token_str=(
                explicit_refresh_token
                if cookie_refresh_token and explicit_refresh_token != cookie_refresh_token
                else None
            ),
            cache=cache,
        )
    except SharedSecurityCacheUnavailable:
        response = JSONResponse(
            status_code=503,
            content={"code": "LOGOUT_PARTIAL", "detail": "本机登录凭证已清除，但服务端会话撤销未完整，请重试"},
        )
        clear_auth_cookies(response)
        return response

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
    operator_id: uuid.UUID = Depends(get_current_account_id),
    cache: AsyncRedisCache = Depends(get_redis_cache),
    _permission: None = Depends(require_permission("account:manage")),
):
    """管理员为指定账户生成一次性密码重置令牌，存入 Redis（1 小时有效）。"""
    try:
        result = await generate_password_reset(
            db=db,
            account_id_str=body.account_id,
            tenant_id=tenant_id,
            cache=cache,
            operator_id=str(operator_id),
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
    db: AsyncSession = Depends(get_db_with_bypass),
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
    """兼容历史后端短链，重定向到 canonical Admin 重置密码页面。"""
    return RedirectResponse(
        settings.build_admin_url(
            "/reset-password",
            {"token": token, "account_id": account_id},
        )
    )
