"""认证业务逻辑服务层。

从 api/v1/auth.py 抽取的核心业务逻辑，遵循四层架构：
API 层只负责请求解析和响应构建，业务逻辑全部在此处理。
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.auth_security import AuthSession
from app.models.export_log import ExportLog
from app.models.gmv import GmvAttributionConfirmation
from app.models.pilot_milestone import PilotAuthorityReceipt
from app.models.retrospective import Retrospective
from app.models.tenant import Account, Tenant, TenantStatus
from app.services.redis_cache import AsyncRedisCache, SharedSecurityCacheUnavailable
from app.services.tenant import get_tenant
from app.utils import utcnow
from app.utils.email import normalize_email
from app.utils.security import (
    AUTH_SESSION_CACHE_PREFIX,
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
    for role in ("admin", "operator", "distributor", "store_guide", "viewer"):
        if role in role_names:
            return role
    return "viewer"


async def _get_account_with_roles(db: AsyncSession, account_id: uuid.UUID) -> Account | None:
    result = await db.execute(select(Account).options(selectinload(Account.roles)).where(Account.id == account_id))
    return result.scalar_one_or_none()


async def revoke_current_tenant_account_sessions(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Revoke one account's durable families inside its runtime transaction."""

    if db.get_bind().dialect.name != "postgresql":
        return 0
    revoked = await db.scalar(select(func.revoke_current_tenant_account_sessions(account_id)))
    return int(revoked or 0)


async def _prune_expired_auth_sessions(db: AsyncSession, *, limit: int = 256) -> None:
    """Bound retention work without deleting sessions referenced by audit facts."""

    if db.get_bind().dialect.name == "postgresql":
        # Export and confirmed-GMV rows are immutable authority evidence. Their
        # session reference must remain durable after the refresh family
        # expires. Serialize against authority functions using the same
        # per-session advisory-lock namespace, and avoid waiting behind another
        # cleanup worker with SKIP LOCKED.
        await db.execute(
            text(
                """WITH candidates AS MATERIALIZED (
                  SELECT session.tenant_id,session.id
                  FROM public.auth_sessions AS session
                  WHERE session.expires_at <= CURRENT_TIMESTAMP
                  AND NOT EXISTS (
                    SELECT 1 FROM public.export_logs AS export
                    WHERE export.tenant_id=session.tenant_id
                      AND export.auth_session_id=session.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.gmv_attribution_confirmations AS confirmation
                    WHERE confirmation.tenant_id=session.tenant_id
                      AND confirmation.auth_session_id=session.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.pilot_authority_receipts AS receipt
                    WHERE receipt.actor_tenant_id=session.tenant_id
                      AND receipt.auth_session_id=session.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.retrospectives AS retrospective
                    WHERE retrospective.completed_actor_tenant_id=session.tenant_id
                      AND retrospective.completed_auth_session_id=session.id
                  )
                  ORDER BY session.expires_at,session.id
                  FOR UPDATE SKIP LOCKED
                  LIMIT :limit
                ), deletable AS MATERIALIZED (
                  SELECT candidate.tenant_id,candidate.id
                  FROM candidates AS candidate
                  WHERE pg_try_advisory_xact_lock(
                    hashtextextended('auth-session:'||candidate.id::text,0)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.export_logs AS export
                    WHERE export.tenant_id=candidate.tenant_id
                      AND export.auth_session_id=candidate.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.gmv_attribution_confirmations AS confirmation
                    WHERE confirmation.tenant_id=candidate.tenant_id
                      AND confirmation.auth_session_id=candidate.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.pilot_authority_receipts AS receipt
                    WHERE receipt.actor_tenant_id=candidate.tenant_id
                      AND receipt.auth_session_id=candidate.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.retrospectives AS retrospective
                    WHERE retrospective.completed_actor_tenant_id=candidate.tenant_id
                      AND retrospective.completed_auth_session_id=candidate.id
                  )
                )
                DELETE FROM public.auth_sessions AS session
                USING deletable
                WHERE session.tenant_id=deletable.tenant_id
                  AND session.id=deletable.id"""
            ),
            {"limit": limit},
        )
        return

    expired_ids = (
        select(AuthSession.id)
        .where(
            AuthSession.expires_at <= datetime.now(UTC),
            ~exists().where(
                ExportLog.tenant_id == AuthSession.tenant_id,
                ExportLog.auth_session_id == AuthSession.id,
            ),
            ~exists().where(
                GmvAttributionConfirmation.tenant_id == AuthSession.tenant_id,
                GmvAttributionConfirmation.auth_session_id == AuthSession.id,
            ),
            ~exists().where(
                PilotAuthorityReceipt.actor_tenant_id == AuthSession.tenant_id,
                PilotAuthorityReceipt.auth_session_id == AuthSession.id,
            ),
            ~exists().where(
                Retrospective.completed_actor_tenant_id == AuthSession.tenant_id,
                Retrospective.completed_auth_session_id == AuthSession.id,
            ),
        )
        .order_by(AuthSession.expires_at)
        .limit(limit)
    )
    await db.execute(delete(AuthSession).where(AuthSession.id.in_(expired_ids)))


def _build_token_pair(account: Account, tenant_type: str, session_id: uuid.UUID | None = None) -> dict:
    """构建 access + refresh token 对。"""
    session_id = session_id or uuid.uuid4()
    token_context = {
        "auth_version": account.auth_version,
        "must_change_password": account.must_change_password,
        "sid": str(session_id),
        "tenant_id": str(account.tenant_id),
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
        # 密码已验证通过，锁定信息不构成枚举泄露；给用户可行动的出路
        remaining_minutes = max(1, -((now - account.locked_until).total_seconds() // 60))
        raise AuthError(
            423,
            f"登录失败次数过多，账户已临时锁定，请约 {remaining_minutes} 分钟后再试或联系管理员重置密码",
        )

    if not account.is_active:
        raise AuthError(403, "账户已停用，请联系租户管理员")

    tenant = await get_tenant(db, account.tenant_id)
    if tenant is None or tenant.status != TenantStatus.active:
        raise AuthError(403, "租户已停用，请联系平台管理员")

    # 登录成功：重置失败计数
    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    tenant_type = tenant.tenant_type.value
    await _prune_expired_auth_sessions(db)
    token_pair = _build_token_pair(account, tenant_type)
    refresh_payload = decode_token(token_pair["refresh_token"])
    db.add(
        AuthSession(
            id=uuid.UUID(refresh_payload["sid"]),
            account_id=account.id,
            tenant_id=account.tenant_id,
            auth_version=account.auth_version,
            current_refresh_jti=refresh_payload["jti"],
            expires_at=datetime.fromtimestamp(refresh_payload["exp"], tz=UTC),
        )
    )
    await db.commit()
    return token_pair


# ---------------------------------------------------------------------------
# Token 刷新
# ---------------------------------------------------------------------------


def _refresh_expiry(payload: dict) -> datetime:
    expires_at = payload.get("exp")
    if expires_at:
        return datetime.fromtimestamp(expires_at, tz=UTC)
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)


def _refresh_session_id(payload: dict) -> uuid.UUID:
    raw_session_id = payload.get("sid") or payload.get("jti")
    try:
        return uuid.UUID(str(raw_session_id))
    except (TypeError, ValueError) as exc:
        raise AuthError(401, "刷新会话无效") from exc


async def _lock_or_adopt_auth_session(
    db: AsyncSession,
    *,
    payload: dict,
    account: Account,
) -> AuthSession:
    """Lock the family row, adopting one pre-session-family token on first use."""

    session_id = _refresh_session_id(payload)
    from app.core.database import lock_auth_session_serialization

    await lock_auth_session_serialization(db, session_id)
    statement = select(AuthSession).where(AuthSession.id == session_id).with_for_update()
    auth_session = (await db.execute(statement)).scalar_one_or_none()
    if auth_session is not None:
        return auth_session

    try:
        async with db.begin_nested():
            db.add(
                AuthSession(
                    id=session_id,
                    account_id=account.id,
                    tenant_id=account.tenant_id,
                    auth_version=account.auth_version,
                    current_refresh_jti=payload["jti"],
                    expires_at=_refresh_expiry(payload),
                )
            )
            await db.flush()
    except IntegrityError:
        # A concurrent request adopted the same legacy family. Lock and inspect
        # its now-authoritative current JTI instead of accepting both requests.
        pass
    auth_session = (await db.execute(statement)).scalar_one_or_none()
    if auth_session is None:
        raise AuthError(401, "刷新会话无效")
    return auth_session


async def _cache_revoke_auth_session(
    cache: AsyncRedisCache,
    session_id: uuid.UUID,
    expires_at: datetime,
) -> None:
    # SQLite drops timezone information even for timezone-aware columns. Keep
    # the security TTL calculation deterministic across SQLite and PostgreSQL.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    remaining = max(1, int((expires_at - datetime.now(UTC)).total_seconds()))
    await cache.revoke_token(f"{AUTH_SESSION_CACHE_PREFIX}{session_id}", ttl=remaining)


async def _reject_and_revoke_replayed_family(
    db: AsyncSession,
    auth_session: AuthSession,
    cache: AsyncRedisCache,
) -> None:
    auth_session.revoked_at = datetime.now(UTC)
    await db.commit()
    try:
        await _cache_revoke_auth_session(cache, auth_session.id, auth_session.expires_at)
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "刷新服务暂时不可用，请稍后重试") from exc
    raise AuthError(401, "刷新会话已撤销，请重新登录")


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

    # 先校验持久化账户和租户状态，再锁定并轮换 refresh family。
    old_jti = payload.get("jti")
    if not old_jti:
        raise AuthError(401, "刷新令牌无效")

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
    auth_session = await _lock_or_adopt_auth_session(db, payload=payload, account=account)
    if (
        auth_session.account_id != account.id
        or auth_session.tenant_id != account.tenant_id
        or auth_session.auth_version != account.auth_version
        or auth_session.revoked_at is not None
    ):
        await _reject_and_revoke_replayed_family(db, auth_session, cache)
    if auth_session.current_refresh_jti != old_jti:
        await _reject_and_revoke_replayed_family(db, auth_session, cache)

    tenant_type = tenant.tenant_type.value if tenant else "brand"
    token_pair = _build_token_pair(account, tenant_type, auth_session.id)
    new_refresh_payload = decode_token(token_pair["refresh_token"])
    auth_session.current_refresh_jti = new_refresh_payload["jti"]
    auth_session.expires_at = _refresh_expiry(new_refresh_payload)
    await db.commit()
    return token_pair


# ---------------------------------------------------------------------------
# 登出
# ---------------------------------------------------------------------------


async def logout_session(
    db: AsyncSession,
    access_token: str | None,
    refresh_token_str: str | None,
    cache: AsyncRedisCache,
    additional_refresh_token_str: str | None = None,
) -> None:
    """Revoke the refresh family, then revoke every presented access session."""
    refresh_payloads: dict[uuid.UUID, dict] = {}
    for candidate in (refresh_token_str, additional_refresh_token_str):
        if not candidate:
            continue
        try:
            refresh_payload = decode_token(candidate)
        except Exception:
            continue
        if not refresh_payload or refresh_payload.get("type") != "refresh" or not refresh_payload.get("jti"):
            continue
        refresh_payloads[_refresh_session_id(refresh_payload)] = refresh_payload

    # Persist every presented family before touching the cache. Browser rollout
    # can temporarily present a legacy body token and a different current
    # HttpOnly cookie; logout must revoke both rather than guessing precedence.
    for refresh_payload in refresh_payloads.values():
        await _persist_logout_session_revocation(db, refresh_payload)
    for session_id, refresh_payload in refresh_payloads.items():
        await _cache_revoke_auth_session(
            cache,
            session_id,
            _refresh_expiry(refresh_payload),
        )

    access_payload: dict | None = None
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
            access_session_id = access_payload.get("sid")
            if access_session_id:
                access_expiry = (
                    datetime.fromtimestamp(access_exp, tz=UTC)
                    if access_exp
                    else datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
                )
                await _cache_revoke_auth_session(cache, uuid.UUID(str(access_session_id)), access_expiry)


async def _persist_logout_session_revocation(db: AsyncSession, payload: dict) -> None:
    """Persist one monotonic family revocation through the control boundary."""

    async def _revoke(session: AsyncSession) -> None:
        session_id = _refresh_session_id(payload)
        from app.core.database import lock_auth_session_serialization

        await lock_auth_session_serialization(session, session_id)
        auth_session = (
            await session.execute(select(AuthSession).where(AuthSession.id == session_id).with_for_update())
        ).scalar_one_or_none()
        if auth_session is None:
            try:
                account_id = uuid.UUID(str(payload.get("sub")))
            except (TypeError, ValueError):
                return
            account = await session.get(Account, account_id)
            if account is None:
                return
            auth_session = AuthSession(
                id=session_id,
                account_id=account.id,
                tenant_id=account.tenant_id,
                auth_version=int(payload.get("auth_version", account.auth_version)),
                current_refresh_jti=payload["jti"],
                expires_at=_refresh_expiry(payload),
            )
            session.add(auth_session)
        auth_session.revoked_at = datetime.now(UTC)
        await session.commit()

    if db.get_bind().dialect.name != "postgresql":
        await _revoke(db)
        return

    from sqlalchemy import text

    from app.core.database import control_session_factory

    async with control_session_factory() as control_db:
        await control_db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await control_db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        await _revoke(control_db)


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

    result = await db.execute(
        select(Account).where(Account.id == account_uuid, Account.tenant_id == tenant_id).with_for_update()
    )
    if not result.scalar_one_or_none():
        raise AuthError(404, "账户不存在")

    # 签发审计与令牌发布共享同一个事务边界。账户行锁（以及初始管理员
    # 激活调用方持有的 opening 行锁）必须在发布缓存记录之后才释放，才能保证
    # 并发重签严格按数据库锁顺序覆盖，而不会出现“先返回的新链接被旧请求
    # 最后写回 Redis”这一倒序窗口。
    from app.services.audit import write_audit_log

    await write_audit_log(
        db,
        operator_id=operator_id or str(tenant_id),
        target_tenant_id=str(tenant_id),
        action="generate_reset_token_requested",
        resource=f"account:{account_uuid}",
    )
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=RESET_TOKEN_TTL)
    cache_key = f"{RESET_TOKEN_KEY_PREFIX}:{account_uuid}"
    record = {
        "token_hash": hash_reset_token(token),
        "account_id": str(account_uuid),
        "tenant_id": str(tenant_id),
        "expires_at": expires_at.isoformat(),
        "activate_account": activate_account,
        "activation_opening_id": str(activation_opening_id) if activation_opening_id else None,
    }
    try:
        await cache.set_shared(cache_key, record, ttl=RESET_TOKEN_TTL)
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "密码重置服务暂时不可用，请稍后重试") from exc
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        # 只删除本次刚写入且仍匹配的记录；若另一个请求已经写入更新的
        # token，consume_shared 的比较删除会保留那个更新值。
        try:
            await cache.consume_shared(cache_key, record)
        except Exception:
            pass
        raise
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
    # 速率限制：与其他认证边界一致，跨 worker 共享且 Redis 不可用时 fail-closed
    # 返回 503，而不是让 SharedSecurityCacheUnavailable 冒泡成 500。
    try:
        allowed, _ = await cache.rate_limit_check_shared(
            f"reset_rate:{account_id_str}", max_attempts=5, window_seconds=60
        )
    except SharedSecurityCacheUnavailable as exc:
        raise AuthError(503, "密码重置服务暂时不可用，请稍后重试") from exc
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
