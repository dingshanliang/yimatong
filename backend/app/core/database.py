import re
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

_is_pg = settings.database_url.startswith("postgresql")

_engine_kwargs: dict = {"echo": False}
if _is_pg:
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)

engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        from app.core.context import get_request_tenant_id

        tenant_id = get_request_tenant_id()
        if tenant_id and _is_pg:
            from sqlalchemy import text

            # asyncpg does not support parameterized SET LOCAL.
            # Validate safe characters before f-string to prevent injection.
            # Valid tenant_id: UUID (0-9a-f-) or platform admin string (a-z_).
            validated_id = str(tenant_id)
            if not re.match(r'^[a-zA-Z0-9_-]+$', validated_id):
                raise ValueError(f"Invalid tenant_id format: {validated_id}")
            await session.execute(
                text(f"SET LOCAL app.tenant_id = '{validated_id}'")
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_with_bypass() -> AsyncGenerator[AsyncSession, None]:
    """Open a session with explicit RLS bypass (for platform admin / background workers)."""
    async with async_session_factory() as session:
        if _is_pg:
            from sqlalchemy import text

            await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
