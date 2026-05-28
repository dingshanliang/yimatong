from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime.

    All business datetime columns use DateTime(timezone=True) (TIMESTAMPTZ).
    This helper ensures consistent UTC-aware timestamps for DB writes.
    """
    return datetime.now(UTC)
