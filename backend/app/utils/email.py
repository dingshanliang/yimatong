"""Canonical account email identity helpers."""


def normalize_email(value: str) -> str:
    """Return the one persisted and queried representation for account email."""

    return value.strip().lower()
