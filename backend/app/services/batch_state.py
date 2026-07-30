"""码批次状态机"""

from app.models.code import CodeBatchStatus

TRANSITIONS: dict[CodeBatchStatus, set[CodeBatchStatus]] = {
    CodeBatchStatus.pending: {CodeBatchStatus.generating},
    CodeBatchStatus.generating: {CodeBatchStatus.completed, CodeBatchStatus.failed},
    CodeBatchStatus.completed: {CodeBatchStatus.printing, CodeBatchStatus.activated},
    CodeBatchStatus.printing: {CodeBatchStatus.delivered, CodeBatchStatus.activated},
    CodeBatchStatus.delivered: {CodeBatchStatus.activated},
    CodeBatchStatus.activated: set(),
    CodeBatchStatus.failed: set(),
}


class InvalidBatchStateTransitionError(Exception):
    pass


def get_valid_batch_transitions(current: CodeBatchStatus) -> set[CodeBatchStatus]:
    return TRANSITIONS.get(current, set())


def can_transition_batch(
    current: CodeBatchStatus,
    target: CodeBatchStatus,
    raise_on_invalid: bool = False,
) -> bool:
    valid = TRANSITIONS.get(current, set())
    if target in valid:
        return True
    if raise_on_invalid:
        raise InvalidBatchStateTransitionError(f"Cannot transition batch from '{current.value}' to '{target.value}'")
    return False
