from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        # Set RLS tenant context if available
        from app.core.context import get_request_tenant_id

        tenant_id = get_request_tenant_id()
        if tenant_id:
            from sqlalchemy import text

            # Begin an explicit transaction that lasts the entire request
            # so SET LOCAL remains in effect for all subsequent queries.
            await session.begin()
            await session.execute(
                text(f"SET LOCAL app.tenant_id = '{tenant_id}'"),
            )
        yield session
