import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Executable

from app.core.config import settings

_is_pg = settings.database_url.startswith("postgresql")

_engine_kwargs: dict = {"echo": False}
if _is_pg:
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)

engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_control_database_url = settings.control_database_url or settings.migration_database_url or settings.database_url
_control_is_pg = _control_database_url.startswith("postgresql")
_control_engine_kwargs: dict = {"echo": False}
if _control_is_pg:
    _control_engine_kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800)
control_engine = create_async_engine(_control_database_url, **_control_engine_kwargs)
control_session_factory = async_sessionmaker(control_engine, class_=AsyncSession, expire_on_commit=False)


def _session_uses_postgresql(session: AsyncSession) -> bool:
    """Return whether the concrete session bind uses PostgreSQL.

    Tests can override ``get_db`` with a different engine, so the session bind
    is the authoritative answer for request-scoped RLS helpers.
    """

    return session.get_bind().dialect.name == "postgresql"


async def set_session_tenant_context(session: AsyncSession, tenant_id: uuid.UUID | str) -> uuid.UUID:
    """Apply one validated tenant context to a restricted runtime session.

    This is used by self-authenticating public/scan-token endpoints after they
    have derived the tenant from trusted server-side evidence. Runtime
    principals have no SET privilege on the independent bypass parameter.

    Note: this only sets ``app.tenant_id``; it does not clear ``app.bypass_rls``.
    RLS safety on a fresh runtime session comes from the strict policy
    (``tenant_id = current_tenant_id() OR (NULL AND bypass)``): a non-NULL
    tenant context makes the bypass branch unreachable regardless of the bypass
    flag. Callers must open a fresh session (as ``get_db`` does) rather than
    reuse one that may carry a leftover bypass setting.
    """

    validated_tenant_id = uuid.UUID(str(tenant_id))
    await _apply_tenant_context(session, validated_tenant_id)
    return validated_tenant_id


async def lock_active_tenant_context(session: AsyncSession, tenant_id: uuid.UUID | str) -> uuid.UUID:
    """Scope a trusted public request and linearize its business transaction.

    Public credentials resolve their tenant inside the route, after dependency
    setup. Business writes and external delivery preparation must pass through
    this seam once the trusted tenant is known. The tenant row lock is held by
    the caller's transaction through its commit, so a platform expiry update
    and the public business action have one deterministic order.

    Consent grant/withdrawal, read-only access, and recovery callbacks do not
    use this seam because they must remain available after plan expiry.
    """

    validated_tenant_id = await set_session_tenant_context(session, tenant_id)
    from app.services.entitlement import require_active_plan

    await require_active_plan(session, validated_tenant_id, lock_tenant=True)
    return validated_tenant_id


async def _apply_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Parameterized SET LOCAL for the validated tenant id.

    ``asyncpg`` does not accept a bound parameter for ``SET LOCAL``, so the
    value is rendered through ``set_config(..., true)`` which does. The tenant
    id is always a validated UUID here; callers must validate before reaching
    this helper. Never build the statement with an f-string interpolation of an
    untrusted or unvalidated value.
    """

    if _session_uses_postgresql(session):
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


async def bootstrap_tenant_row(
    session: AsyncSession,
    statement: Executable,
    parameters: dict[str, Any] | None = None,
):
    """Resolve one tenant-owned row, then immediately lock the transaction to it.

    This is the only supported two-phase bootstrap for public credentials,
    callbacks and worker identifiers.  Callers must supply an exact, unique
    server-side lookup and the selected ORM row must expose ``tenant_id``.
    No business query may run between this helper and the scoped phase.
    """

    bootstrap_session = session
    owns_bootstrap_session = False
    if _session_uses_postgresql(session):
        bootstrap_session = control_session_factory()
        owns_bootstrap_session = True
        await bootstrap_session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await bootstrap_session.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
    try:
        result = await bootstrap_session.execute(statement, parameters or {})
        row = result.scalar_one_or_none()
    except Exception:
        # Bootstrap is required to be the first operation in the transaction;
        # rollback clears its transaction-local bypass without masking the
        # original lookup failure with an aborted-transaction error.
        await bootstrap_session.rollback()
        raise
    finally:
        if owns_bootstrap_session:
            await bootstrap_session.close()
    if row is None:
        return None
    tenant_id = getattr(row, "tenant_id", None)
    if tenant_id is None:
        raise RuntimeError("Tenant bootstrap row has no tenant_id")
    await set_session_tenant_context(session, tenant_id)
    if owns_bootstrap_session:
        scoped_result = await session.execute(statement, parameters or {})
        return scoped_result.scalar_one_or_none()
    return row


async def bootstrap_tenant_keys(
    session: AsyncSession,
    statement: Executable,
    parameters: dict[str, Any] | None = None,
) -> list[tuple[Any, uuid.UUID]]:
    """Read a bounded global work index as ``(object_id, tenant_id)`` pairs.

    Global pollers may only use the returned identifiers to open independent
    tenant-scoped transactions.  They must not mutate business rows through
    this bootstrap session.
    """

    bootstrap_session = session
    owns_bootstrap_session = False
    if _session_uses_postgresql(session):
        bootstrap_session = control_session_factory()
        owns_bootstrap_session = True
        await bootstrap_session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await bootstrap_session.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
    try:
        rows = list((await bootstrap_session.execute(statement, parameters or {})).all())
    except Exception:
        await bootstrap_session.rollback()
        raise
    finally:
        if owns_bootstrap_session:
            await bootstrap_session.close()
    return [(row[0], uuid.UUID(str(row[1]))) for row in rows]


_PLAN_RECOVERY_WRITE_PATHS = frozenset(
    {
        "/api/v1/auth/change-password",
        "/api/v1/auth/logout",
        "/api/v1/agency/exit-context",
    }
)


async def _lock_request_tenants(session: AsyncSession, request: Request, tenant_id: uuid.UUID) -> None:
    """Lock the acting and business tenants in one stable order."""

    from app.models.tenant import Tenant, TenantStatus

    original_tenant_id = getattr(request.state, "original_tenant_id", None)
    tenant_ids = {tenant_id}
    if original_tenant_id:
        tenant_ids.add(uuid.UUID(str(original_tenant_id)))
    for locked_tenant_id in sorted(tenant_ids, key=str):
        await _apply_tenant_context(session, locked_tenant_id)
        locked_tenant = await session.scalar(select(Tenant).where(Tenant.id == locked_tenant_id).with_for_update())
        if locked_tenant is None or locked_tenant.status != TenantStatus.active:
            raise HTTPException(status_code=401, detail="Tenant context is no longer valid")
    await _apply_tenant_context(session, tenant_id)


async def _revalidate_durable_session(request: Request, principal_tenant_id: uuid.UUID) -> None:
    """Fail closed when a durable JWT family was revoked while middleware ran."""

    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        # Legacy access tokens remain valid for their original short lifetime,
        # matching the middleware rollout compatibility rule.
        return
    from app.models.auth_security import AuthSession

    try:
        validated_session_id = uuid.UUID(str(session_id))
        account_id = uuid.UUID(str(request.state.account_id))
    except (AttributeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid login session") from exc
    async with control_session_factory() as control_db:
        active_session = await control_db.scalar(
            select(AuthSession.id).where(
                AuthSession.id == validated_session_id,
                AuthSession.account_id == account_id,
                AuthSession.tenant_id == principal_tenant_id,
                AuthSession.auth_version == int(getattr(request.state, "auth_version", 0)),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > func.now(),
            )
        )
    if active_session is None:
        raise HTTPException(status_code=401, detail="登录会话已撤销或过期")


async def _revalidate_acting_authorization(request: Request) -> None:
    """Recheck a live agency grant after both tenant rows are locked."""

    acting_tenant_id = getattr(request.state, "acting_tenant_id", None)
    original_tenant_id = getattr(request.state, "original_tenant_id", None)
    if not acting_tenant_id or not original_tenant_id or request.url.path == "/api/v1/agency/exit-context":
        return
    from app.middleware.tenant import TenantScopeMiddleware
    from app.models.tenant import AgencyAuthorization, AgencyAuthStatus

    agency_id = uuid.UUID(str(original_tenant_id))
    client_id = uuid.UUID(str(acting_tenant_id))
    async with control_session_factory() as control_db:
        await control_db.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await control_db.execute(text("SELECT set_config('app.bypass_rls', 'true', true)"))
        authorization = await control_db.scalar(
            select(AgencyAuthorization).where(
                AgencyAuthorization.agency_tenant_id == agency_id,
                AgencyAuthorization.client_tenant_id == client_id,
                AgencyAuthorization.status == AgencyAuthStatus.active,
            )
        )
    if authorization is None:
        raise HTTPException(status_code=403, detail="代运营授权已失效")
    expires_at = authorization.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=403, detail="代运营授权已失效")
    live_scopes = list(authorization.scope)
    if not TenantScopeMiddleware._acting_path_is_explicitly_supported(
        request.url.path,
        live_scopes,
        request.method,
    ):
        raise HTTPException(status_code=403, detail="当前代运营授权不允许访问该功能")
    request.state.agency_scopes = live_scopes


async def _revalidate_mutating_principal(session: AsyncSession, request: Request, tenant_id: uuid.UUID) -> None:
    """Revalidate the JWT principal at the serialized mutation boundary."""

    if getattr(request.state, "auth_method", None) != "jwt":
        return
    from app.models.tenant import Account
    from app.services.auth import resolve_account_role

    principal_tenant_id = uuid.UUID(str(getattr(request.state, "original_tenant_id", None) or tenant_id))
    try:
        account_id = uuid.UUID(str(request.state.account_id))
    except (AttributeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid account context") from exc
    # Keep the same lock order as refresh/logout: durable family first, then
    # the tenant account row. This avoids AuthSession <-> Account deadlocks.
    await _revalidate_durable_session(request, principal_tenant_id)
    await _apply_tenant_context(session, principal_tenant_id)
    account = await session.scalar(
        select(Account)
        .options(selectinload(Account.roles))
        .where(Account.id == account_id, Account.tenant_id == principal_tenant_id)
        .with_for_update()
    )
    if (
        account is None
        or account.is_active is False
        or account.auth_version != int(getattr(request.state, "auth_version", 0))
        or resolve_account_role(account) != getattr(request.state, "role", None)
    ):
        raise HTTPException(status_code=401, detail="账户已停用或登录状态已失效")
    await _revalidate_acting_authorization(request)
    await _apply_tenant_context(session, tenant_id)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        from app.core.context import get_request_tenant_id

        tenant_id = get_request_tenant_id()
        if tenant_id and _is_pg:
            # Validate strict UUID format (or the safe "platform" sentinel)
            # before any statement reaches the database. The actual statement
            # is rendered parameterized via set_config in _apply_tenant_context.
            validated_id = str(tenant_id)
            if validated_id not in ("platform",):
                try:
                    uuid.UUID(validated_id)
                except ValueError:
                    raise ValueError(f"Invalid tenant_id format: {validated_id}")
            # "platform" is a non-UUID control-plane sentinel that must not be
            # passed through _apply_tenant_context's UUID path; set it directly
            # with the same parameterized helper form.
            if validated_id == "platform":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": "platform"},
                )
            else:
                await _apply_tenant_context(session, uuid.UUID(validated_id))
        try:
            # Hold the authoritative tenant row through commit for every
            # authenticated business mutation. This closes the middleware
            # check/write race without blocking read-only access or the exact
            # authentication recovery operations an expired tenant needs.
            if tenant_id and str(tenant_id) != "platform" and request.method not in {"GET", "HEAD", "OPTIONS"}:
                validated_tenant_id = uuid.UUID(str(tenant_id))
                if _session_uses_postgresql(session):
                    # Global quota epoch is always first; only then may this
                    # request lock its complete, precomputed Tenant set.
                    from app.services.quota import lock_quota_rollout_state

                    await lock_quota_rollout_state(session)
                    await _lock_request_tenants(session, request, validated_tenant_id)
                    await _revalidate_mutating_principal(session, request, validated_tenant_id)
                if request.url.path not in _PLAN_RECOVERY_WRITE_PATHS:
                    from app.services.entitlement import require_active_plan

                    await require_active_plan(session, validated_tenant_id, lock_tenant=True)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_with_bypass(db: AsyncSession = Depends(get_db)) -> AsyncGenerator[AsyncSession, None]:
    """Open a session with explicit RLS bypass (for platform admin / background workers)."""
    # Unit tests override get_db with their transaction-scoped SQLite session.
    if not _is_pg:
        yield db
        return
    async with control_session_factory() as session:
        if _control_is_pg:
            from sqlalchemy import text

            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_for_auth(db: AsyncSession = Depends(get_db)) -> AsyncGenerator[AsyncSession, None]:
    """Controlled cross-tenant session used only to authenticate login/refresh tokens.

    Public authentication requests do not yet have a tenant RLS context. The
    calling service must constrain every query by credentials or token subject.
    """
    async for session in get_db_with_bypass(db):
        yield session


async def get_db_for_ops(db: AsyncSession = Depends(get_db)) -> AsyncGenerator[AsyncSession, None]:
    """Request session for ops.

    Agency callers remain on their own RLS context.  Individual ops services
    may open a control session for exact key discovery and must then switch to
    a customer-scoped runtime transaction.  Platform callers explicitly open
    their control-plane session at the endpoint/service boundary.
    """
    yield db


async def get_db_for_consumer() -> AsyncGenerator[AsyncSession, None]:
    """Open a session for consumer endpoints with tenant context from scan_token.

    Uses RLS (same as get_db) instead of bypass.
    """
    async with async_session_factory() as session:
        from app.core.context import (
            get_request_tenant_id,
            reset_consumer_tenant_id,
            set_consumer_tenant_id,
        )

        context_guard = set_consumer_tenant_id(None)

        tenant_id = get_request_tenant_id()
        if tenant_id and _is_pg:
            validated_id = str(tenant_id)
            try:
                parsed = uuid.UUID(validated_id)
            except ValueError as exc:
                raise ValueError(f"Invalid tenant_id format: {validated_id}") from exc
            await _apply_tenant_context(session, parsed)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            reset_consumer_tenant_id(context_guard)
