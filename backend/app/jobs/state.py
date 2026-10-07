"""Job status transitions (step 1.C.1).

`queued -> running -> succeeded | failed | cancelled`. A single function
guards every transition; illegal ones raise. A failed job can never become
`succeeded` — this is AGENTS.md #65 encoded here, not left to a comment.
"""

from app.models.job import JobStatus

_LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    # QUEUED -> FAILED covers a job that never starts running: an unknown
    # job type, or the resource-manager hook refusing a second heavy job.
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class IllegalJobTransition(Exception):
    """Raised when a job status transition is not in `_LEGAL_TRANSITIONS`."""

    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        super().__init__(f"cannot transition job from '{current.value}' to '{target.value}'")
        self.current = current
        self.target = target


def guard_transition(current: JobStatus, target: JobStatus) -> None:
    """Raise `IllegalJobTransition` unless `current -> target` is legal."""
    if target not in _LEGAL_TRANSITIONS[current]:
        raise IllegalJobTransition(current, target)
