"""Durable authentication state for the isolated Platform control plane."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_security import PlatformAuthSession


class PlatformSessionUnavailable(Exception):
    """The presented platform session cannot be authoritatively revoked."""


async def revoke_platform_session(db: AsyncSession, session_id: str | None) -> None:
    """Durably revoke one live Platform session before the browser cookie is cleared."""

    try:
        validated_session_id = uuid.UUID(session_id)
    except (TypeError, ValueError) as exc:
        raise PlatformSessionUnavailable("Invalid platform session id") from exc

    result = await db.execute(
        update(PlatformAuthSession)
        .where(
            PlatformAuthSession.id == validated_session_id,
        )
        .values(revoked_at=datetime.now(UTC))
    )
    if result.rowcount != 1:
        raise PlatformSessionUnavailable("Platform session is no longer active")
    await db.commit()
