"""Focused code export contract tests."""

import inspect

import pytest

from app.services.code_export import generate_code_csv, spreadsheet_safe


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=cmd|' /C calc'!A0", "'=cmd|' /C calc'!A0"),
        (" +SUM(1,2)", "' +SUM(1,2)"),
        ("\t-1+1", "'\t-1+1"),
        ("@IMPORTXML(example)", "'@IMPORTXML(example)"),
        ("安全产品", "安全产品"),
        ("", ""),
    ],
)
def test_spreadsheet_safe_neutralizes_formula_prefixes(value, expected):
    assert spreadsheet_safe(value) == expected


def test_authoritative_export_query_has_no_silent_row_limit():
    assert ".limit(" not in inspect.getsource(generate_code_csv)


def test_authoritative_export_query_does_not_lock_runtime_code_items():
    source = inspect.getsource(generate_code_csv)
    item_query = source[source.index("items = list(") : source.index("_validate_item_contract")]

    assert ".with_for_update()" not in item_query
