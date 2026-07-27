"""权威四状态生命周期状态机（yimatong-zgb1.3）。

与 ``app.services.code_state``（旧 6 值状态机）并存：
- 旧状态机保留以维持 AC4 兼容期（现有调用方不破坏）；
- 本模块提供权威四状态（unactivated/active/frozen/voided）转换规则，调用方既可
  传旧 ``CodeItemStatus``（自动经 ``to_lifecycle`` 映射），也可直接传 ``CodeLifecycle``。

权威合约（THREE_LINE_PRODUCT_SPEC Decision 10）：
- unactivated → {active, voided}  可激活或作废
- active → {frozen, voided}        可冻结或作废
- frozen → {active, voided}        可恢复或作废（冻结是临时风控，可逆）
- voided → {}                      终态，不可逆

冻结/解冻需要授权与审计（Decision 18）；作废限制品牌管理员、需原因与二次确认、不可逆。
本模块只判定转换合法性，权限/审计由 service 层与 API 层负责。
"""

from __future__ import annotations

from app.models.code import CodeItemStatus, CodeLifecycle, to_lifecycle

LIFECYCLE_TRANSITIONS: dict[CodeLifecycle, set[CodeLifecycle]] = {
    CodeLifecycle.unactivated: {CodeLifecycle.active, CodeLifecycle.voided},
    CodeLifecycle.active: {CodeLifecycle.frozen, CodeLifecycle.voided},
    CodeLifecycle.frozen: {CodeLifecycle.active, CodeLifecycle.voided},
    CodeLifecycle.voided: set(),
}


class InvalidLifecycleTransitionError(Exception):
    """权威生命周期非法转换。"""


def get_valid_lifecycle_transitions(current: CodeLifecycle) -> set[CodeLifecycle]:
    return LIFECYCLE_TRANSITIONS.get(current, set())


def can_lifecycle_transition(
    current: CodeItemStatus | CodeLifecycle | str,
    target: CodeLifecycle,
    raise_on_invalid: bool = False,
) -> bool:
    """判定能否从 current（旧值或权威值）转换到 target（权威值）。

    先把 current 经 ``to_lifecycle`` 归一化到权威四状态，再查 LIFECYCLE_TRANSITIONS。
    """
    current_lifecycle = current if isinstance(current, CodeLifecycle) else to_lifecycle(current)
    valid = LIFECYCLE_TRANSITIONS.get(current_lifecycle, set())
    if target in valid:
        return True
    if raise_on_invalid:
        raise InvalidLifecycleTransitionError(
            f"Cannot transition lifecycle from '{current_lifecycle.value}' to '{target.value}'"
        )
    return False
