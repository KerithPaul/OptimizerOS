"""Every illegal job-status transition raises (step 1.C.1 verify)."""

import pytest

from app.jobs.state import IllegalJobTransition, guard_transition
from app.models.job import JobStatus

_ALL_TRANSITIONS = [(a, b) for a in JobStatus for b in JobStatus]
_LEGAL = {
    (JobStatus.QUEUED, JobStatus.RUNNING),
    (JobStatus.QUEUED, JobStatus.FAILED),
    (JobStatus.QUEUED, JobStatus.CANCELLED),
    (JobStatus.RUNNING, JobStatus.SUCCEEDED),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.CANCELLED),
}
_ILLEGAL = [pair for pair in _ALL_TRANSITIONS if pair not in _LEGAL and pair[0] != pair[1]]


@pytest.mark.parametrize("current,target", sorted(_LEGAL, key=str))
def test_legal_transitions_do_not_raise(current: JobStatus, target: JobStatus) -> None:
    guard_transition(current, target)


@pytest.mark.parametrize("current,target", sorted(_ILLEGAL, key=str))
def test_illegal_transitions_raise(current: JobStatus, target: JobStatus) -> None:
    with pytest.raises(IllegalJobTransition):
        guard_transition(current, target)


def test_failed_can_never_become_succeeded() -> None:
    with pytest.raises(IllegalJobTransition):
        guard_transition(JobStatus.FAILED, JobStatus.SUCCEEDED)
