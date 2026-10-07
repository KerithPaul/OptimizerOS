"""Shared helpers that resolve `agent_runs` when a job stops early.

A terminal `jobs` row does not automatically resolve the `agent_runs` rows
it seeded (`app.api.v1.agents.start_agent_run` / `changes.apply_change`
mark the seed row `running` before the job even leaves `queued`). Cancel
and handler-failure paths walk those rows to a terminal status so the UI
stops showing a run that will never finish.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentRun, AgentRunStatus
from app.models.job import Job, JobStatus


def _resolve_running_agent_runs(
    db: Session,
    job_id: int,
    status: AgentRunStatus,
    *,
    error: str | None = None,
    stopped_reason: str | None = None,
) -> None:
    rows = list(
        db.scalars(
            select(AgentRun).where(
                AgentRun.job_id == job_id, AgentRun.status == AgentRunStatus.RUNNING
            )
        )
    )
    if not rows:
        return
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = status
        row.finished_at = now
        if error is not None:
            row.error = error
        if stopped_reason is not None:
            row.stopped_reason = stopped_reason
    db.commit()


def cancel_running_agent_runs(db: Session, job_id: int) -> None:
    _resolve_running_agent_runs(db, job_id, AgentRunStatus.CANCELLED)


def fail_running_agent_runs(db: Session, job_id: int, *, error: str | None = None) -> None:
    _resolve_running_agent_runs(
        db,
        job_id,
        AgentRunStatus.FAILED,
        error=error,
        stopped_reason="job_failed",
    )


def reconcile_orphaned_agent_runs(db: Session) -> int:
    """Mark seed rows `failed`/`cancelled` when their job already ended.

    `apply_change` / `start_agent_run` insert the seed row as `running`
    before the worker sees the job. If the worker dies, or a handler
    exception used to skip this walk, the UI keeps polling `running`.
    """
    rows = list(
        db.scalars(select(AgentRun).where(AgentRun.status == AgentRunStatus.RUNNING))
    )
    if not rows:
        return 0
    terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
    now = datetime.now(timezone.utc)
    resolved = 0
    for row in rows:
        if row.job_id is None:
            continue
        job = db.get(Job, row.job_id)
        if job is None or job.status not in terminal:
            continue
        if job.status is JobStatus.CANCELLED:
            row.status = AgentRunStatus.CANCELLED
        else:
            row.status = AgentRunStatus.FAILED
            row.stopped_reason = "job_failed"
            if row.error is None:
                row.error = job.error or "job ended while this run was still marked running"
        row.finished_at = now
        resolved += 1
    if resolved:
        db.commit()
    return resolved
