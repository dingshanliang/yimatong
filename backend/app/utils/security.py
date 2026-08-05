import re
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt
from starlette.responses import Response

from app.core.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def validate_password_strength(password: str) -> None:
    """验证密码强度。不符合要求时抛出 ValueError。"""
    if len(password) < 8:
        raise ValueError("密码至少需要 8 位")
    if not re.search(r"[a-zA-Z]", password):
        raise ValueError("密码必须包含字母")
    if not re.search(r"\d", password):
        raise ValueError("密码必须包含数字")


def create_access_token(
    tenant_id: str,
    account_id: str,
    role: str,
    tenant_type: str | None = None,
    extra: dict | None = None,
) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    resolved_tenant_type = tenant_type
    if resolved_tenant_type is None:
        resolved_tenant_type = (
            "platform"
            if tenant_id == "platform" and account_id == "platform-admin" and role == "platform_admin"
            else "brand"
        )
    payload = {
        "sub": str(account_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "tenant_type": resolved_tenant_type,
        "exp": expire,
        "type": "access",
        "jti": str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(account_id: str, extra: dict | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(account_id),
        "exp": expire,
        "type": "refresh",
        "jti": jti,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])


async def verify_access_token(token: str) -> dict | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        jti = payload.get("jti")
        if jti:
            from app.services.redis_cache import AsyncRedisCache

            cache = AsyncRedisCache()
            if await cache.is_token_revoked(jti):
                return None
        return payload
    except JWTError:
        return None


async def verify_refresh_token(token: str) -> dict | None:
    """验证 refresh token，检查黑名单。"""
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            return None
        jti = payload.get("jti")
        if jti:
            from app.services.redis_cache import AsyncRedisCache

            cache = AsyncRedisCache()
            if await cache.is_token_revoked(jti):
                return None
        return payload
    except JWTError:
        return None


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str | None = None,
    max_age_access: int = 900,
    max_age_refresh: int = 30 * 86400,
) -> None:
    """设置 HttpOnly 认证 cookie。"""
    domain = settings.cookie_domain or None
    secure = settings.cookie_secure
    samesite = settings.cookie_samesite

    response.set_cookie(
        "access_token",
        access_token,
        max_age=max_age_access,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    if refresh_token:
        response.set_cookie(
            "refresh_token",
            refresh_token,
            max_age=max_age_refresh,
            httponly=True,
            secure=secure,
            samesite=samesite,
            domain=domain,
            path="/api/v1/auth/refresh",  # 只在刷新时发送
        )


def clear_auth_cookies(response: Response) -> None:
    """清除认证 cookie。"""
    domain = settings.cookie_domain or None
    response.delete_cookie("access_token", domain=domain, path="/")
    response.delete_cookie("refresh_token", domain=domain, path="/api/v1/auth/refresh")
