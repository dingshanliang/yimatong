from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime.

    All business datetime columns use DateTime(timezone=True) (TIMESTAMPTZ).
    This helper ensures consistent UTC-aware timestamps for DB writes.
    """
    return datetime.now(UTC)


def escape_like_pattern(value: str) -> str:
    """Escape SQL LIKE wildcards in a user-provided search string.

    Returns a string safe to pass to ``column.ilike(f"%{escaped}%", escape="\\")``.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
