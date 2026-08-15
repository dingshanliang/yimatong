from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.pilot_milestone import MilestoneCorrectionRequest
from app.schemas.retrospective import RetrospectiveUpdateRequest


def test_correction_rejects_naive_or_future_timestamp_and_extra_fields():
    base = {
        "milestone_type": "launched",
        "corrected_at": datetime.now(UTC) - timedelta(minutes=1),
        "source": "operator evidence",
        "reason": "fix source timestamp",
    }
    for patch in (
        {"corrected_at": datetime.now()},
        {"corrected_at": datetime.now(UTC) + timedelta(days=1)},
        {"unexpected": "ignored-before-u09"},
    ):
        with pytest.raises(ValidationError):
            MilestoneCorrectionRequest.model_validate({**base, **patch})


def test_retrospective_rejects_empty_unknown_and_unbounded_action_payloads():
    for payload in (
        {},
        {"unknown": True},
        {"actions": [{"content": "x", "status": "not-a-status"}]},
        {"actions": [{"content": "x" * 1001}]},
        {"actions": [{"content": "x"}] * 101},
    ):
        with pytest.raises(ValidationError):
            RetrospectiveUpdateRequest.model_validate(payload)
