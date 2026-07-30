"""码状态机完整单元测试

测试 app/services/code_state.py 中的 TRANSITIONS、InvalidStateTransitionError、
get_valid_transitions() 和 can_transition()。

CodeItemStatus 枚举值：created, activated, bound, expired, revoked, frozen

合法转换：
  created   → {activated, revoked}
  activated → {bound, revoked, frozen}
  bound     → {expired, revoked, frozen}
  frozen    → {activated, revoked}
  expired   → {}  (终态)
  revoked   → {}  (终态)
"""

import pytest

from app.models.code import CodeItemStatus
from app.services.code_state import (
    TRANSITIONS,
    InvalidStateTransitionError,
    can_transition,
    get_valid_transitions,
)

# ── 所有合法转换（source, target） ──────────────────────────────────

VALID_TRANSITIONS: list[tuple[CodeItemStatus, CodeItemStatus]] = [
    (CodeItemStatus.created, CodeItemStatus.activated),
    (CodeItemStatus.created, CodeItemStatus.revoked),
    (CodeItemStatus.activated, CodeItemStatus.bound),
    (CodeItemStatus.activated, CodeItemStatus.revoked),
    (CodeItemStatus.activated, CodeItemStatus.frozen),
    (CodeItemStatus.bound, CodeItemStatus.expired),
    (CodeItemStatus.bound, CodeItemStatus.revoked),
    (CodeItemStatus.bound, CodeItemStatus.frozen),
    (CodeItemStatus.frozen, CodeItemStatus.activated),
    (CodeItemStatus.frozen, CodeItemStatus.revoked),
]

TERMINAL_STATES = [CodeItemStatus.expired, CodeItemStatus.revoked]
ALL_STATES = list(CodeItemStatus)
VALID_SET = {(s, t) for s, t in VALID_TRANSITIONS}

# 所有非法转换对（排除合法转换和自身转换）
INVALID_TRANSITIONS: list[tuple[CodeItemStatus, CodeItemStatus]] = [
    (source, target)
    for source in ALL_STATES
    for target in ALL_STATES
    if source != target and (source, target) not in VALID_SET
]


class TestGetValidTransitions:
    """get_valid_transitions(current) 返回合法目标集合"""

    @pytest.mark.parametrize("source", ALL_STATES)
    def test_returns_set_for_every_status(self, source: CodeItemStatus):
        result = get_valid_transitions(source)
        assert isinstance(result, set)
        for item in result:
            assert isinstance(item, CodeItemStatus)

    def test_created_transitions(self):
        valid = get_valid_transitions(CodeItemStatus.created)
        assert valid == {CodeItemStatus.activated, CodeItemStatus.revoked}

    def test_activated_transitions(self):
        valid = get_valid_transitions(CodeItemStatus.activated)
        assert valid == {CodeItemStatus.bound, CodeItemStatus.revoked, CodeItemStatus.frozen}

    def test_bound_transitions(self):
        valid = get_valid_transitions(CodeItemStatus.bound)
        assert valid == {CodeItemStatus.expired, CodeItemStatus.revoked, CodeItemStatus.frozen}

    def test_frozen_transitions(self):
        valid = get_valid_transitions(CodeItemStatus.frozen)
        assert valid == {CodeItemStatus.activated, CodeItemStatus.revoked}

    def test_terminal_states_return_empty(self):
        """终态 (expired, revoked) 不可转换"""
        for state in TERMINAL_STATES:
            assert get_valid_transitions(state) == set(), f"{state.value} should have no transitions"

    def test_unknown_status_returns_empty(self):
        """不在 TRANSITIONS 字典中的状态返回空集"""
        result = get_valid_transitions("nonexistent")  # type: ignore[arg-type]
        assert result == set()


class TestCanTransitionValid:
    """所有合法转换都能通过 can_transition"""

    @pytest.mark.parametrize(
        "source,target",
        VALID_TRANSITIONS,
        ids=[f"{s.value}->{t.value}" for s, t in VALID_TRANSITIONS],
    )
    def test_valid_transition_returns_true(self, source: CodeItemStatus, target: CodeItemStatus):
        assert can_transition(source, target) is True

    @pytest.mark.parametrize(
        "source,target",
        VALID_TRANSITIONS,
        ids=[f"{s.value}->{t.value}" for s, t in VALID_TRANSITIONS],
    )
    def test_valid_transition_does_not_raise(self, source: CodeItemStatus, target: CodeItemStatus):
        """raise_on_invalid=True 时合法转换不抛异常"""
        assert can_transition(source, target, raise_on_invalid=True) is True


class TestCanTransitionInvalid:
    """所有非法转换 can_transition 返回 False"""

    @pytest.mark.parametrize(
        "source,target",
        INVALID_TRANSITIONS,
        ids=[f"{s.value}->{t.value}" for s, t in INVALID_TRANSITIONS],
    )
    def test_invalid_transition_returns_false(self, source: CodeItemStatus, target: CodeItemStatus):
        assert can_transition(source, target) is False

    def test_invalid_transition_does_not_raise_by_default(self):
        """can_transition 默认不抛异常"""
        result = can_transition(CodeItemStatus.created, CodeItemStatus.bound)
        assert result is False

    def test_self_transition_returns_false(self):
        """自身转换是非法的"""
        for state in ALL_STATES:
            assert can_transition(state, state) is False, f"{state.value} -> self should be False"


class TestCanTransitionRaiseOnInvalid:
    """can_transition(raise_on_invalid=True) 对非法转换抛 InvalidStateTransitionError"""

    def test_raises_for_invalid_transition(self):
        with pytest.raises(InvalidStateTransitionError):
            can_transition(CodeItemStatus.created, CodeItemStatus.bound, raise_on_invalid=True)

    def test_error_message_contains_states(self):
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            can_transition(CodeItemStatus.created, CodeItemStatus.bound, raise_on_invalid=True)
        msg = str(exc_info.value).lower()
        assert "created" in msg
        assert "bound" in msg

    def test_raises_for_terminal_to_any(self):
        """终态不能转到任何状态"""
        for terminal in TERMINAL_STATES:
            for target in ALL_STATES:
                if terminal == target:
                    continue
                with pytest.raises(InvalidStateTransitionError):
                    can_transition(terminal, target, raise_on_invalid=True)


class TestTransitionsDictCompleteness:
    """TRANSITIONS 字典完整性检查"""

    def test_all_statuses_in_dict(self):
        """每个 CodeItemStatus 枚举值都出现在 TRANSITIONS 中"""
        for status in CodeItemStatus:
            assert status in TRANSITIONS, f"{status.value} missing from TRANSITIONS"

    def test_no_extra_keys(self):
        """TRANSITIONS 没有多余的键"""
        assert set(TRANSITIONS.keys()) == set(CodeItemStatus)

    def test_values_are_sets_of_enums(self):
        """每个值都是 CodeItemStatus 的 set"""
        for key, value_set in TRANSITIONS.items():
            assert isinstance(value_set, set)
            for item in value_set:
                assert isinstance(item, CodeItemStatus)
