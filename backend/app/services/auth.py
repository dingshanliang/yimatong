"""认证业务逻辑服务层。

从 api/v1/auth.py 抽取的核心业务逻辑，遵循四层架构：
API 层只负责请求解析和响应构建，业务逻辑全部在此处理。
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.tenant import Account, Tenant
from app.services.redis_cache import AsyncRedisCache
from app.services.tenant import get_tenant
from app.utils import utcnow
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_password,
    verify_refresh_token,
)

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15
RESET_TOKEN_TTL = 3600  # 1 小时
RESET_TOKEN_KEY_PREFIX = "reset"

# 有效 bcrypt hash，用于不存在的账户的恒定时间比较
_DUMMY_BCRYPT_HASH = "$2b$12$ALZ2Z98JlD2ezSiz5/K0Ge1fOp0BI.nO4yChDQiEJnhBZgoT8JE8i"


class AuthError(Exception):
    """认证业务逻辑错误。"""

    def __init__(self, code: int, detail: str, headers: dict | None = None):
        self.code = code
        self.detail = detail
        self.headers = headers or {}
        super().__init__(detail)


def hash_reset_token(token: str) -> str:
    """密码重置令牌的 SHA-256 哈希。"""
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_account_role(account: Account) -> str:
    """从 Account 的 roles 关系中解析最高权限角色。"""
    role_names = {role.name for role in account.roles}
    for role in ("platform_admin", "admin", "operator"):
        if role in role_names:
            return role
    return "operator" if "operator" in role_names else "viewer"


async def _get_account_with_roles(db: AsyncSession, account_id: uuid.UUID) -> Account | None:
    result = await db.execute(select(Account).options(selectinload(Account.roles)).where(Account.id == account_id))
    return result.scalar_one_or_none()


def _build_token_pair(account: Account, tenant_type: str) -> dict:
    """构建 access + refresh token 对。"""
    token_context = {"auth_version": account.auth_version}
    access = create_access_token(
        str(account.tenant_id),
        str(account.id),
        resolve_account_role(account),
        tenant_type,
        extra=token_context,
    )
    refresh = create_refresh_token(str(account.id), extra=token_context)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------


async def authenticate_login(
    db: AsyncSession,
    email: str,
    password: str,
    tenant_slug: str | None,
    client_ip: str,
    cache: AsyncRedisCache,
) -> dict:
    """处理登录认证。

    Returns:
        token_pair dict（含 access_token, refresh_token 等）

    Raises:
        AuthError: 认证失败（速率限制、密码错误、账户锁定等）
    """
    # 速率限制
    allowed, _ = await cache.rate_limit_check(f"login_rate:{client_ip}", max_attempts=20, window_seconds=60)
    if not allowed:
        raise AuthError(429, "登录尝试过于频繁，请稍后再试", headers={"Retry-After": "60"})

    # 查询账户
    query = select(Account).options(selectinload(Account.roles)).where(Account.email == email)
    if tenant_slug:
        query = query.join(Tenant, Tenant.id == Account.tenant_id).where(Tenant.slug == tenant_slug)
    result = await db.execute(query)
    account = result.scalars().first()

    now = utcnow()

    # 恒定时间密码验证
    target_hash = account.hashed_password if account else _DUMMY_BCRYPT_HASH
    if not verify_password(password, target_hash):
        if account:
            account.failed_login_attempts += 1
            if account.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                account.locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
            await db.commit()
        raise AuthError(401, "邮箱或密码不正确")

    assert account is not None  # 已通过密码验证，一定存在

    if account.locked_until and account.locked_until > now:
        raise AuthError(401, "邮箱或密码不正确")

    if not account.is_active:
        raise AuthError(403, "账户已停用，请联系租户管理员")

    # 登录成功：重置失败计数
    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    await db.commit()

    tenant = await get_tenant(db, account.tenant_id)
    tenant_type = tenant.tenant_type.value if tenant else "brand"
    return _build_token_pair(account, tenant_type)


# ---------------------------------------------------------------------------
# Token 刷新
# ---------------------------------------------------------------------------


async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str | None,
    cache: AsyncRedisCache,
) -> dict:
    """刷新 access token。

    Returns:
        token_pair dict

    Raises:
        AuthError: 刷新失败
    """
    if not refresh_token:
        raise AuthError(401, "缺少刷新令牌")

    payload = await verify_refresh_token(refresh_token)
    if not payload:
        raise AuthError(401, "刷新令牌无效")

    # 将旧 refresh token 加入黑名单（轮换）
    old_jti = payload.get("jti")
    if old_jti:
        old_exp = payload.get("exp")
        if old_exp:
            remaining = max(1, int(old_exp - datetime.now(UTC).timestamp()))
        else:
            remaining = settings.refresh_token_expire_days * 86400
        await cache.revoke_token(old_jti, ttl=remaining)

    account_id = payload["sub"]
    account = await _get_account_with_roles(db, uuid.UUID(account_id))
    if not account:
        raise AuthError(401, "账户不存在")
    if not account.is_active:
        raise AuthError(401, "账户已停用，请重新联系管理员")
    if payload.get("auth_version", 0) != account.auth_version:
        raise AuthError(401, "登录状态已失效，请重新登录")

    tenant = await get_tenant(db, account.tenant_id)
    tenant_type = tenant.tenant_type.value if tenant else "brand"
    return _build_token_pair(account, tenant_type)


# ---------------------------------------------------------------------------
# 登出
# ---------------------------------------------------------------------------


async def logout_session(
    access_token: str | None,
    refresh_token_str: str | None,
    cache: AsyncRedisCache,
) -> None:
    """撤销 access token 和 refresh token。"""
    if not access_token:
        return

    try:
        payload = decode_token(access_token)
    except Exception:
        return

    jti = payload.get("jti")
    if not jti:
        return

    exp = payload.get("exp")
    remaining = max(1, int(exp - datetime.now(UTC).timestamp())) if exp else settings.access_token_expire_minutes * 60
    await cache.revoke_token(jti, ttl=remaining)

    # 撤销关联的 refresh token
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
            pass


# ---------------------------------------------------------------------------
# 密码重置
# ---------------------------------------------------------------------------


async def generate_password_reset(
    db: AsyncSession,
    account_id_str: str,
    tenant_id: uuid.UUID,
    cache: AsyncRedisCache,
) -> dict:
    """管理员生成密码重置令牌。

    Returns:
        {"reset_token": ..., "reset_url": ...}

    Raises:
        AuthError: 生成失败
    """
    try:
        account_uuid = uuid.UUID(account_id_str)
    except ValueError:
        raise AuthError(400, "账户 ID 格式无效")

    result = await db.execute(select(Account).where(Account.id == account_uuid, Account.tenant_id == tenant_id))
    if not result.scalar_one_or_none():
        raise AuthError(404, "账户不存在")

    token = secrets.token_urlsafe(32)
    await cache.set(
        f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}",
        {"token_hash": hash_reset_token(token), "account_id": str(account_uuid), "tenant_id": str(tenant_id)},
        ttl=RESET_TOKEN_TTL,
    )
    reset_url = f"{settings.base_url}/api/v1/auth/reset-page?token={token}&account_id={account_uuid}"

    # 审计日志
    try:
        from app.services.audit import write_audit_log

        await write_audit_log(
            db,
            operator_id=str(tenant_id),
            target_tenant_id=str(tenant_id),
            action="generate_reset_token",
            resource=f"account:{account_uuid}",
        )
        await db.commit()
    except Exception:
        pass

    return {"reset_token": token, "reset_url": reset_url}


async def confirm_password_reset(
    db: AsyncSession,
    token: str,
    account_id_str: str,
    new_password: str,
    client_ip: str,
    cache: AsyncRedisCache,
) -> dict:
    """用户通过重置令牌设置新密码。

    Returns:
        {"status": "ok", "message": "..."}

    Raises:
        AuthError: 重置失败
    """
    # 速率限制
    allowed, _ = await cache.rate_limit_check(f"reset_rate:{account_id_str}", max_attempts=5, window_seconds=60)
    if not allowed:
        raise AuthError(429, "重置尝试过于频繁，请稍后再试", headers={"Retry-After": "60"})

    # 验证密码强度
    try:
        validate_password_strength(new_password)
    except ValueError as e:
        raise AuthError(400, str(e)) from e

    try:
        account_uuid = uuid.UUID(account_id_str)
    except ValueError:
        raise AuthError(400, "账户 ID 格式无效")

    record = await cache.get(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}")
    if not record:
        raise AuthError(400, "重置链接已过期或不存在，请联系管理员重新生成")

    if record.get("token_hash") != hash_reset_token(token) or record.get("account_id") != account_id_str:
        raise AuthError(400, "重置令牌无效")

    # 查找账户（验证租户隔离）
    stored_tenant_id = record.get("tenant_id")
    if stored_tenant_id:
        result = await db.execute(
            select(Account).where(Account.id == account_uuid, Account.tenant_id == uuid.UUID(stored_tenant_id))
        )
    else:
        result = await db.execute(select(Account).where(Account.id == account_uuid))
    account = result.scalar_one_or_none()
    if not account:
        raise AuthError(404, "账户不存在")

    # 更新密码
    account.hashed_password = hash_password(new_password)
    account.failed_login_attempts = 0
    account.locked_until = None
    await db.commit()

    # 立即删除 token（一次性）
    await cache.invalidate(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}")

    # 审计日志
    try:
        from app.services.audit import write_audit_log

        await write_audit_log(
            db,
            operator_id=str(account_uuid),
            target_tenant_id=str(account.tenant_id) if account.tenant_id else "",
            action="password_reset",
            resource=f"account:{account_uuid}",
        )
        await db.commit()
    except Exception:
        pass

    return {"status": "ok", "message": "密码已重置，请使用新密码登录"}
