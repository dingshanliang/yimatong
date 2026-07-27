"""权威四状态生命周期契约单元测试（yimatong-zgb1.3）。

覆盖：
- 6 个旧 CodeItemStatus 值映射到 4 个权威 CodeLifecycle，唯一且明确。
- 合法转换通过、非法转换拒绝。
- voided 终态不可逆。
- 旧字符串值与 enum 都能映射。
"""

from __future__ import annotations

import pytest

from app.models.code import (
    LEGACY_TO_LIFECYCLE,
    CodeItemStatus,
    CodeLifecycle,
    to_lifecycle,
)
from app.services.code_lifecycle import (
    LIFECYCLE_TRANSITIONS,
    InvalidLifecycleTransitionError,
    can_lifecycle_transition,
    get_valid_lifecycle_transitions,
)


class TestLegacyToLifecycleMapping:
    """AC：新旧数据都能映射为唯一、明确的四状态结果。"""

    def test_six_legacy_values_all_map(self):
        assert len(LEGACY_TO_LIFECYCLE) == 6
        for legacy in CodeItemStatus:
            assert legacy in LEGACY_TO_LIFECYCLE, f"{legacy} missing from mapping"

    def test_created_maps_to_unactivated(self):
        assert to_lifecycle(CodeItemStatus.created) == CodeLifecycle.unactivated
        assert to_lifecycle("created") == CodeLifecycle.unactivated

    def test_activated_maps_to_active(self):
        assert to_lifecycle(CodeItemStatus.activated) == CodeLifecycle.active

    def test_bound_maps_to_active(self):
        """bound 是绑定事件，归一化为 active（Decision 11）。"""
        assert to_lifecycle(CodeItemStatus.bound) == CodeLifecycle.active

    def test_frozen_maps_to_frozen(self):
        assert to_lifecycle(CodeItemStatus.frozen) == CodeLifecycle.frozen

    def test_revoked_maps_to_voided(self):
        assert to_lifecycle(CodeItemStatus.revoked) == CodeLifecycle.voided

    def test_expired_maps_to_voided(self):
        """expired 是时间事件，归一化为 voided（Decision 11）。"""
        assert to_lifecycle(CodeItemStatus.expired) == CodeLifecycle.voided

    def test_mapping_targets_only_four_states(self):
        targets = set(LEGACY_TO_LIFECYCLE.values())
        assert targets == {CodeLifecycle.unactivated, CodeLifecycle.active, CodeLifecycle.frozen, CodeLifecycle.voided}

    def test_unknown_string_maps_to_voided_conservatively(self):
        """未知值保守映射为 voided，避免被当作可消费的 active。"""
        assert to_lifecycle("nonexistent-status") == CodeLifecycle.voided

    def test_string_value_maps_correctly(self):
        assert to_lifecycle("activated") == CodeLifecycle.active
        assert to_lifecycle("frozen") == CodeLifecycle.frozen


class TestLifecycleTransitions:
    """AC：非法状态转换被拒绝，合法转换规则有自动化测试。"""

    def test_exactly_four_lifecycle_states(self):
        assert set(LIFECYCLE_TRANSITIONS) == {
            CodeLifecycle.unactivated,
            CodeLifecycle.active,
            CodeLifecycle.frozen,
            CodeLifecycle.voided,
        }

    @pytest.mark.parametrize(
        "current,target",
        [
            (CodeLifecycle.unactivated, CodeLifecycle.active),
            (CodeLifecycle.unactivated, CodeLifecycle.voided),
            (CodeLifecycle.active, CodeLifecycle.frozen),
            (CodeLifecycle.active, CodeLifecycle.voided),
            (CodeLifecycle.frozen, CodeLifecycle.active),
            (CodeLifecycle.frozen, CodeLifecycle.voided),
        ],
    )
    def test_legal_transitions_allowed(self, current, target):
        assert can_lifecycle_transition(current, target) is True

    @pytest.mark.parametrize(
        "current,target",
        [
            # 不能跳过激活
            (CodeLifecycle.unactivated, CodeLifecycle.frozen),
            # 不能"反激活"
            (CodeLifecycle.active, CodeLifecycle.unactivated),
            # frozen 不能直接回 unactivated（必须先回 active）
            (CodeLifecycle.frozen, CodeLifecycle.unactivated),
            # voided 终态不可逆
            (CodeLifecycle.voided, CodeLifecycle.active),
            (CodeLifecycle.voided, CodeLifecycle.unactivated),
            (CodeLifecycle.voided, CodeLifecycle.frozen),
            # 自转换非法
            (CodeLifecycle.active, CodeLifecycle.active),
            (CodeLifecycle.unactivated, CodeLifecycle.unactivated),
        ],
    )
    def test_illegal_transitions_rejected(self, current, target):
        assert can_lifecycle_transition(current, target) is False

    def test_raise_on_invalid_raises(self):
        with pytest.raises(InvalidLifecycleTransitionError):
            can_lifecycle_transition(CodeLifecycle.voided, CodeLifecycle.active, raise_on_invalid=True)

    def test_voided_is_irreversible(self):
        """AC：voided 终态不可逆。"""
        assert get_valid_lifecycle_transitions(CodeLifecycle.voided) == set()
        for target in CodeLifecycle:
            assert can_lifecycle_transition(CodeLifecycle.voided, target) is False

    def test_accepts_legacy_status_input(self):
        """旧 CodeItemStatus 直接传入也能判定（自动映射）。"""
        # created(unactivated) → active 合法
        assert can_lifecycle_transition(CodeItemStatus.created, CodeLifecycle.active) is True
        # revoked(voided) → active 非法
        assert can_lifecycle_transition(CodeItemStatus.revoked, CodeLifecycle.active) is False
        # bound(active) → frozen 合法（归一化后）
        assert can_lifecycle_transition(CodeItemStatus.bound, CodeLifecycle.frozen) is True

    def test_accepts_string_input(self):
        assert can_lifecycle_transition("activated", CodeLifecycle.frozen) is True
        assert can_lifecycle_transition("revoked", CodeLifecycle.active) is False
