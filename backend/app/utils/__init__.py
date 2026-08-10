from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

CHINA_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime.

    All business datetime columns use DateTime(timezone=True) (TIMESTAMPTZ).
    This helper ensures consistent UTC-aware timestamps for DB writes.
    """
    return datetime.now(UTC)


def china_business_date(at: datetime | None = None) -> date:
    """Return the calendar date used for China-facing business rules."""
    instant = at if at is not None else datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("Business-date instant must be timezone-aware")
    return instant.astimezone(CHINA_BUSINESS_TIMEZONE).date()


def escape_like_pattern(value: str) -> str:
    """Escape SQL LIKE wildcards in a user-provided search string.

    Returns a string safe to pass to ``column.ilike(f"%{escaped}%", escape="\\")``.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
