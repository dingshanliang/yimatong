"""Focused authoritative packaging code batch state machine tests."""

import pytest

from app.models.code import CodeBatchStatus
from app.services.batch_state import InvalidBatchStateTransitionError, can_transition_batch


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CodeBatchStatus.pending, CodeBatchStatus.generating),
        (CodeBatchStatus.generating, CodeBatchStatus.completed),
        (CodeBatchStatus.generating, CodeBatchStatus.failed),
        (CodeBatchStatus.completed, CodeBatchStatus.exported),
        (CodeBatchStatus.exported, CodeBatchStatus.printing),
        (CodeBatchStatus.printing, CodeBatchStatus.delivered),
        (CodeBatchStatus.delivered, CodeBatchStatus.activated),
    ],
)
def test_authoritative_forward_transition_is_allowed(current, target):
    assert can_transition_batch(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (CodeBatchStatus.completed, CodeBatchStatus.printing),
        (CodeBatchStatus.completed, CodeBatchStatus.activated),
        (CodeBatchStatus.printing, CodeBatchStatus.activated),
        (CodeBatchStatus.exported, CodeBatchStatus.delivered),
        (CodeBatchStatus.failed, CodeBatchStatus.generating),
    ],
)
def test_authoritative_state_machine_rejects_skips_and_retries(current, target):
    with pytest.raises(InvalidBatchStateTransitionError):
        can_transition_batch(current, target, raise_on_invalid=True)
