"""Durable authentication state for the isolated Platform control plane."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_security import PlatformAuthSession
from app.models.pilot_milestone import PilotAuthorityReceipt, PilotMilestoneCorrection


class PlatformSessionUnavailable(Exception):
    """The presented platform session cannot be authoritatively revoked."""


async def prune_expired_platform_sessions(db: AsyncSession, *, limit: int = 256) -> None:
    """Delete bounded expired sessions unless immutable pilot evidence references them."""

    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text(
                """WITH candidates AS MATERIALIZED (
                  SELECT session.id FROM public.platform_auth_sessions session
                  WHERE session.expires_at<=CURRENT_TIMESTAMP
                    AND NOT EXISTS (SELECT 1 FROM public.pilot_authority_receipts receipt
                      WHERE receipt.platform_auth_session_id=session.id)
                    AND NOT EXISTS (SELECT 1 FROM public.pilot_milestone_corrections correction
                      WHERE correction.platform_auth_session_id=session.id)
                  ORDER BY session.expires_at,session.id FOR UPDATE SKIP LOCKED LIMIT :limit
                ), deletable AS MATERIALIZED (
                  SELECT candidate.id FROM candidates candidate
                  WHERE pg_try_advisory_xact_lock(
                    hashtextextended('platform-auth-session:'||candidate.id::text,0))
                    AND NOT EXISTS (SELECT 1 FROM public.pilot_authority_receipts receipt
                      WHERE receipt.platform_auth_session_id=candidate.id)
                    AND NOT EXISTS (SELECT 1 FROM public.pilot_milestone_corrections correction
                      WHERE correction.platform_auth_session_id=candidate.id)
                ) DELETE FROM public.platform_auth_sessions session USING deletable
                  WHERE session.id=deletable.id"""
            ),
            {"limit": limit},
        )
        return

    expired_ids = (
        select(PlatformAuthSession.id)
        .where(
            PlatformAuthSession.expires_at <= datetime.now(UTC),
            ~exists().where(PilotAuthorityReceipt.platform_auth_session_id == PlatformAuthSession.id),
            ~exists().where(PilotMilestoneCorrection.platform_auth_session_id == PlatformAuthSession.id),
        )
        .order_by(PlatformAuthSession.expires_at)
        .limit(limit)
    )
    await db.execute(delete(PlatformAuthSession).where(PlatformAuthSession.id.in_(expired_ids)))


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
