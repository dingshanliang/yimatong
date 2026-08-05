"""认证业务逻辑服务层。

从 api/v1/auth.py 抽取的核心业务逻辑，遵循四层架构：
API 层只负责请求解析和响应构建，业务逻辑全部在此处理。
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.auth_security import ConsumedRefreshToken
from app.models.tenant import Account, Tenant, TenantStatus
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.services.tenant import get_tenant
from app.utils import utcnow
from app.utils.email import normalize_email
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
    # Platform administrators are not tenant Accounts. A historical tenant role
    # with the same name must never cross the dedicated platform auth boundary.
    for role in ("admin", "operator"):
        if role in role_names:
            return role
    return "operator" if "operator" in role_names else "viewer"


async def _get_account_with_roles(db: AsyncSession, account_id: uuid.UUID) -> Account | None:
    result = await db.execute(select(Account).options(selectinload(Account.roles)).where(Account.id == account_id))
    return result.scalar_one_or_none()


def _build_token_pair(account: Account, tenant_type: str) -> dict:
    """构建 access + refresh token 对。"""
    token_context = {
        "auth_version": account.auth_version,
        "must_change_password": account.must_change_password,
    }
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
    email = normalize_email(email)
    # IP + account identifier limits must be shared by every worker. Email is
    # normalized and hashed so the Redis key does not expose account PII.
    email_hash = hashlib.sha256(email.encode()).hexdigest()
    try:
        ip_allowed, _ = await cache.rate_limit_check_shared(f"login:ip:{client_ip}", max_attempts=20, window_seconds=60)
        email_allowed, _ = await cache.rate_limit_check_shared(
            f"login:email:{email_hash}", max_attempts=20, window_seconds=60
        )
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "登录服务暂时不可用，请稍后重试") from exc
    if not ip_allowed or not email_allowed:
        raise AuthError(429, "登录尝试过于频繁，请稍后再试", headers={"Retry-After": "60"})

    # Lock the selected account rows so concurrent failures cannot lose
    # increments or issue a token while another request is locking the account.
    query = select(Account).options(selectinload(Account.roles)).where(Account.email == email)
    if tenant_slug:
        query = query.join(Tenant, Tenant.id == Account.tenant_id).where(Tenant.slug == tenant_slug)
    result = await db.execute(query.order_by(Account.id).with_for_update())
    accounts = list(result.scalars().all())

    if not tenant_slug and len(accounts) > 1:
        # A wrong password must not reveal that the email belongs to multiple
        # workspaces. Only a password matching at least one candidate may ask
        # the caller to disambiguate the workspace.
        matching_accounts = [account for account in accounts if verify_password(password, account.hashed_password)]
        if not matching_accounts:
            raise AuthError(401, "邮箱或密码不正确")
        raise AuthError(409, "该邮箱关联多个工作区，请填写工作区标识")

    account = accounts[0] if accounts else None

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

    tenant = await get_tenant(db, account.tenant_id)
    if tenant is None or tenant.status != TenantStatus.active:
        raise AuthError(403, "租户已停用，请联系平台管理员")

    # 登录成功：重置失败计数
    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    await db.commit()

    tenant_type = tenant.tenant_type.value
    return _build_token_pair(account, tenant_type)


# ---------------------------------------------------------------------------
# Token 刷新
# ---------------------------------------------------------------------------


async def _consume_refresh_jti(db: AsyncSession, jti: str, expires_at: datetime) -> bool:
    """Persist a global refresh-token consumption claim.

    Returns False when another refresh/logout request already consumed it.
    """
    await db.execute(delete(ConsumedRefreshToken).where(ConsumedRefreshToken.expires_at <= datetime.now(UTC)))
    try:
        async with db.begin_nested():
            db.add(ConsumedRefreshToken(jti=jti, expires_at=expires_at))
            await db.flush()
    except IntegrityError:
        return False
    return True


async def _persist_logout_refresh_revocation(db: AsyncSession, jti: str, expires_at: datetime) -> bool:
    """Persist logout's global refresh claim outside the tenant transaction.

    PostgreSQL protects ``consumed_refresh_tokens`` as control-plane state, so
    an authenticated tenant session must never write it directly.  SQLite unit
    tests keep using the supplied transaction-scoped session.
    """
    if db.get_bind().dialect.name != "postgresql":
        claimed = await _consume_refresh_jti(db, jti, expires_at)
        await db.commit()
        return claimed

    from sqlalchemy import text

    from app.core.database import control_session_factory

    async with control_session_factory() as control_db:
        await control_db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await control_db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        claimed = await _consume_refresh_jti(control_db, jti, expires_at)
        await control_db.commit()
        return claimed


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

    try:
        payload = await verify_refresh_token(refresh_token)
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "刷新服务暂时不可用，请稍后重试") from exc
    if not payload:
        raise AuthError(401, "刷新令牌无效")

    # 先校验持久化账户和租户状态，再原子消费旧 refresh token。
    old_jti = payload.get("jti")

    account_id = payload["sub"]
    account = await _get_account_with_roles(db, uuid.UUID(account_id))
    if not account:
        raise AuthError(401, "账户不存在")
    if not account.is_active:
        raise AuthError(401, "账户已停用，请重新联系管理员")
    if payload.get("auth_version", 0) != account.auth_version:
        raise AuthError(401, "登录状态已失效，请重新登录")

    tenant = await get_tenant(db, account.tenant_id)
    if tenant is None or tenant.status != TenantStatus.active:
        raise AuthError(401, "租户已停用，请重新联系管理员")
    if old_jti:
        old_exp = payload.get("exp")
        expires_at = (
            datetime.fromtimestamp(old_exp, tz=UTC)
            if old_exp
            else datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
        )
        if not await _consume_refresh_jti(db, old_jti, expires_at):
            raise AuthError(401, "刷新令牌已使用")
    tenant_type = tenant.tenant_type.value if tenant else "brand"
    return _build_token_pair(account, tenant_type)


# ---------------------------------------------------------------------------
# 登出
# ---------------------------------------------------------------------------


async def logout_session(
    db: AsyncSession,
    access_token: str | None,
    refresh_token_str: str | None,
    cache: AsyncRedisCache,
) -> None:
    """Persist refresh revocation, then revoke access in the shared cache."""
    if refresh_token_str:
        try:
            refresh_payload = decode_token(refresh_token_str)
        except Exception:
            refresh_payload = None
        if refresh_payload and refresh_payload.get("type") == "refresh" and refresh_payload.get("jti"):
            refresh_exp = refresh_payload.get("exp")
            refresh_expires_at = (
                datetime.fromtimestamp(refresh_exp, tz=UTC)
                if refresh_exp
                else datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
            )
            await _persist_logout_refresh_revocation(db, refresh_payload["jti"], refresh_expires_at)

    if access_token:
        try:
            access_payload = decode_token(access_token)
        except Exception:
            access_payload = None
        if access_payload and access_payload.get("type") == "access" and access_payload.get("jti"):
            access_exp = access_payload.get("exp")
            remaining = (
                max(1, int(access_exp - datetime.now(UTC).timestamp()))
                if access_exp
                else settings.access_token_expire_minutes * 60
            )
            await cache.revoke_token(access_payload["jti"], ttl=remaining)


# ---------------------------------------------------------------------------
# 密码重置
# ---------------------------------------------------------------------------


async def generate_password_reset(
    db: AsyncSession,
    account_id_str: str,
    tenant_id: uuid.UUID,
    cache: AsyncRedisCache,
    *,
    operator_id: str | None = None,
    activate_account: bool = False,
    activation_opening_id: uuid.UUID | None = None,
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

    # 先持久化签发请求审计；审计失败时不得返回敏感凭证。
    from app.services.audit import write_audit_log

    await write_audit_log(
        db,
        operator_id=operator_id or str(tenant_id),
        target_tenant_id=str(tenant_id),
        action="generate_reset_token_requested",
        resource=f"account:{account_uuid}",
    )
    await db.commit()

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=RESET_TOKEN_TTL)
    try:
        await cache.set_shared(
            f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}",
            {
                "token_hash": hash_reset_token(token),
                "account_id": str(account_uuid),
                "tenant_id": str(tenant_id),
                "expires_at": expires_at.isoformat(),
                "activate_account": activate_account,
                "activation_opening_id": str(activation_opening_id) if activation_opening_id else None,
            },
            ttl=RESET_TOKEN_TTL,
        )
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "密码重置服务暂时不可用，请稍后重试") from exc
    reset_url = settings.build_admin_url(
        "/reset-password",
        {"token": token, "account_id": str(account_uuid)},
    )

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

    try:
        record = await cache.get_shared(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}")
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "密码重置服务暂时不可用，请稍后重试") from exc
    if not record:
        raise AuthError(400, "重置链接已过期或不存在，请联系管理员重新生成")

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

    if record.get("token_hash") != hash_reset_token(token) or record.get("account_id") != account_id_str:
        raise AuthError(400, "重置令牌无效")
    new_password_hash = hash_password(new_password)
    try:
        consumed = await cache.consume_shared(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}", record)
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "密码重置服务暂时不可用，请稍后重试") from exc
    if not consumed:
        raise AuthError(400, "重置令牌已使用或失效")

    try:
        account.hashed_password = new_password_hash
        if record.get("activate_account") is True:
            account.is_active = True
            from app.models.platform_opening import PlatformTenantOpening

            opening_id = record.get("activation_opening_id")
            if not opening_id:
                raise AuthError(400, "初始管理员激活状态已失效")
            opening = (
                await db.execute(
                    select(PlatformTenantOpening)
                    .where(
                        PlatformTenantOpening.id == uuid.UUID(opening_id),
                        PlatformTenantOpening.initial_admin_id == account_uuid,
                        PlatformTenantOpening.initial_admin_state == "pending_activation",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if opening is None:
                raise AuthError(400, "初始管理员激活状态已失效")
            opening.initial_admin_state = "activated"
        account.auth_version += 1
        account.failed_login_attempts = 0
        account.locked_until = None

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
        await db.rollback()
        expires_at_raw = record.get("expires_at")
        try:
            expires_at = datetime.fromisoformat(expires_at_raw) if expires_at_raw else datetime.now(UTC)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            remaining = max(1, int((expires_at - datetime.now(UTC)).total_seconds()))
            await cache.set_shared_if_absent(f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}", record, ttl=remaining)
        except Exception:
            pass
        raise

    return {"status": "ok", "message": "密码已重置，请使用新密码登录"}
