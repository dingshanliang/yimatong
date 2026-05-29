"""ARQ Worker settings for background task processing."""

from arq.connections import RedisSettings

from app.core.config import settings


async def startup(ctx: dict) -> None:
    """Called when the worker starts."""


async def shutdown(ctx: dict) -> None:
    """Called when the worker shuts down."""


async def dummy_task(ctx: dict) -> None:
    """Placeholder task to satisfy arq worker requirement."""


class WorkerSettings:
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [dummy_task]
