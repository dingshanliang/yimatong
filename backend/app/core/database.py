import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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


async def get_db() -> AsyncGenerator[AsyncSession, None]:
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
