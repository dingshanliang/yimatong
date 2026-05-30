from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        from app.core.context import get_request_tenant_id

        tenant_id = get_request_tenant_id()
        if tenant_id:
            from sqlalchemy import text

            # asyncpg does not support parameterized SET LOCAL, but UUID v7
            # only contains [0-9a-f-] so f-string is safe here.
            await session.execute(
                text(f"SET LOCAL app.tenant_id = '{str(tenant_id)}'")
            )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
