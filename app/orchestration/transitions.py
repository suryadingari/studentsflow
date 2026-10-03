"""Allowed workflow-level status transitions."""

from app.schemas.workflow import WorkflowStatus


ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.CREATED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.RUNNING: frozenset({WorkflowStatus.WAITING_FOR_APPROVAL, WorkflowStatus.PAUSED,
                                       WorkflowStatus.COMPLETED, WorkflowStatus.FAILED,
                                       WorkflowStatus.CANCELLED}),
    WorkflowStatus.WAITING_FOR_APPROVAL: frozenset({WorkflowStatus.RUNNING,
                                                    WorkflowStatus.COMPLETED,
                                                    WorkflowStatus.CANCELLED}),
    WorkflowStatus.PAUSED: frozenset({WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED}),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}


def validate_transition(current: WorkflowStatus, target: WorkflowStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid workflow transition: {current.value} -> {target.value}")
