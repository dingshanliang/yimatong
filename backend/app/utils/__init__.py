from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).

    PostgreSQL TIMESTAMP WITHOUT TIME ZONE columns via asyncpg
    reject timezone-aware datetimes. Use this helper everywhere
    a naive UTC timestamp is needed for DB writes.
    """
    return datetime.now(UTC).replace(tzinfo=None)
