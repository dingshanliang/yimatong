import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        from app.core.context import get_request_tenant_id

        tenant_id = get_request_tenant_id()
        if tenant_id:
            from sqlalchemy import text

            await session.execute(
                text("SET LOCAL app.tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
        else:
            logger.debug("Database session opened without tenant context — RLS bypass active")
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def get_db_with_bypass() -> AsyncGenerator[AsyncSession, None]:
    """Open a session with explicit RLS bypass (for platform admin / background workers)."""
    async with async_session_factory() as session:
        from sqlalchemy import text

        await session.execute(text("SET LOCAL app.bypass_rls = 'true'"))
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
