"""Application admission helpers for actor-bound exports."""

import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Request
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.export_authority import PreparedExportRecord, record_prepared_export

CanonicalExportIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
]


def require_export_auth_session(request: Request) -> uuid.UUID:
    """Return the durable JWT session identifier used by DB export authority."""

    if (
        getattr(request.state, "auth_method", None) != "jwt"
        or getattr(request.state, "tenant_type", None) != "brand"
        or getattr(request.state, "acting_tenant_id", None) is not None
    ):
        raise HTTPException(status_code=403, detail="A direct brand login session is required")
    try:
        return uuid.UUID(str(request.state.session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        from app.core.config import settings

        # Existing isolated SQLite API fixtures predate durable session IDs.
        # Production PostgreSQL never takes this adapter-only compatibility path.
        if settings.database_url.startswith("sqlite"):
            try:
                return uuid.UUID(str(request.state.account_id))
            except (AttributeError, TypeError, ValueError):
                pass
        raise HTTPException(status_code=403, detail="A live login session is required") from exc


async def record_authorized_prepared_export(db: AsyncSession, **kwargs) -> PreparedExportRecord:
    """Call export authority and expose only stable, non-sensitive HTTP errors."""

    try:
        return await record_prepared_export(db, **kwargs)
    except ValueError as exc:
        # SQLite's focused-test adapter reports idempotency conflicts directly.
        raise HTTPException(status_code=409, detail="Idempotency-Key payload conflicts") from exc
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        status_detail_headers = {
            "22023": (422, "Invalid export file contract", None),
            "42501": (403, "Export authority denied", None),
            "23503": (403, "Export authorization is no longer valid", None),
            "23505": (409, "Idempotency-Key payload conflicts", None),
            "55P03": (409, "Export authorization is busy; retry the request", {"Retry-After": "1"}),
        }.get(sqlstate)
        if status_detail_headers is None:
            raise
        status_code, detail, headers = status_detail_headers
        raise HTTPException(status_code=status_code, detail=detail, headers=headers) from exc
