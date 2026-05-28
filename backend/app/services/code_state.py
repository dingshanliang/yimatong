"""码状态机"""

from app.models.code import CodeItemStatus

TRANSITIONS: dict[CodeItemStatus, set[CodeItemStatus]] = {
    CodeItemStatus.created: {CodeItemStatus.activated, CodeItemStatus.revoked},
    CodeItemStatus.activated: {CodeItemStatus.bound, CodeItemStatus.revoked, CodeItemStatus.frozen},
    CodeItemStatus.bound: {CodeItemStatus.expired, CodeItemStatus.revoked, CodeItemStatus.frozen},
    CodeItemStatus.frozen: {CodeItemStatus.activated, CodeItemStatus.revoked},
    CodeItemStatus.expired: set(),
    CodeItemStatus.revoked: set(),
}


class InvalidStateTransitionError(Exception):
    pass


def get_valid_transitions(current: CodeItemStatus) -> set[CodeItemStatus]:
    return TRANSITIONS.get(current, set())


def can_transition(
    current: CodeItemStatus,
    target: CodeItemStatus,
    raise_on_invalid: bool = False,
) -> bool:
    valid = TRANSITIONS.get(current, set())
    if target in valid:
        return True
    if raise_on_invalid:
        raise InvalidStateTransitionError(
            f"Cannot transition from '{current.value}' to '{target.value}'"
        )
    return False
