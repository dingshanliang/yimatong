"""Focused code export contract tests."""

import uuid
from types import SimpleNamespace

import pytest

from app.core.exceptions import ConflictError
from app.models.code import CodeBatchSource, CodeBatchStatus, CodeGenerationMode, CodeType
from app.services import code_export
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


@pytest.mark.anyio
async def test_authoritative_export_executes_complete_nonlocking_item_query(monkeypatch):
    class RecordingSession:
        statement = None

        async def scalars(self, statement):
            self.statement = statement
            return []

    batch = SimpleNamespace(
        status=CodeBatchStatus.completed,
        contract_version=1,
        expected_item_count=1,
        source=CodeBatchSource.generated,
        generation_mode=CodeGenerationMode.item_level,
        code_type=CodeType.single,
        quantity=1,
    )

    async def load_batch(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(code_export, "_lock_forward_operational_code_batch", load_batch)
    db = RecordingSession()

    with pytest.raises(ConflictError, match="item count"):
        await generate_code_csv(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert db.statement is not None
    assert db.statement._limit_clause is None
    assert db.statement._for_update_arg is None
