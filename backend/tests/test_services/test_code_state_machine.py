"""A4-004: 码状态机 测试"""

import pytest

from app.models.code import CodeItemStatus
from app.services.code_state import InvalidStateTransitionError, can_transition, get_valid_transitions


class TestCodeStateMachine:
    def test_valid_transitions_from_created(self):
        valid = get_valid_transitions(CodeItemStatus.created)
        assert CodeItemStatus.activated in valid
        assert CodeItemStatus.revoked in valid

    def test_valid_transitions_from_activated(self):
        valid = get_valid_transitions(CodeItemStatus.activated)
        assert CodeItemStatus.bound in valid
        assert CodeItemStatus.revoked in valid

    def test_valid_transitions_from_bound(self):
        valid = get_valid_transitions(CodeItemStatus.bound)
        assert CodeItemStatus.expired in valid
        assert CodeItemStatus.revoked in valid

    def test_no_transitions_from_expired(self):
        valid = get_valid_transitions(CodeItemStatus.expired)
        assert len(valid) == 0

    def test_no_transitions_from_revoked(self):
        valid = get_valid_transitions(CodeItemStatus.revoked)
        assert len(valid) == 0

    def test_invalid_transition_raises(self):
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            can_transition(CodeItemStatus.created, CodeItemStatus.bound, raise_on_invalid=True)
        assert "created" in str(exc_info.value).lower()
        assert "bound" in str(exc_info.value).lower()

    def test_valid_transition_does_not_raise(self):
        result = can_transition(CodeItemStatus.created, CodeItemStatus.activated, raise_on_invalid=True)
        assert result is True

    def test_any_state_can_revoke(self):
        for status in [CodeItemStatus.created, CodeItemStatus.activated, CodeItemStatus.bound]:
            assert can_transition(status, CodeItemStatus.revoked)
