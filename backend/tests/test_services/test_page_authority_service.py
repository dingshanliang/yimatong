"""Focused contracts for the application-facing page authority seam."""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from app.services.page import map_page_authority_db_error


class _DatabaseOriginalError(Exception):
    def __init__(self, sqlstate: str):
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "status_code", "retry_after"),
    [
        ("42501", 403, None),
        ("23503", 404, None),
        ("22023", 422, None),
        ("23514", 409, None),
        ("23505", 409, None),
        ("55P03", 409, "1"),
    ],
)
def test_page_authority_sqlstates_map_without_exposing_database_details(
    sqlstate: str,
    status_code: int,
    retry_after: str | None,
):
    error = DBAPIError("statement", {}, _DatabaseOriginalError(sqlstate), False)

    mapped = map_page_authority_db_error(error)

    assert isinstance(mapped, HTTPException)
    assert mapped.status_code == status_code
    assert "database" not in str(mapped.detail).lower()
    assert (mapped.headers or {}).get("Retry-After") == retry_after


def test_unknown_page_authority_database_error_is_not_relabelled():
    error = DBAPIError("statement", {}, _DatabaseOriginalError("XX000"), False)

    assert map_page_authority_db_error(error) is None
