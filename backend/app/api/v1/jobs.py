"""Job creation and progress SSE endpoint.

`GET /api/v1/jobs/{id}/events` is `[CONFIRMED C1]`. The event payload shape
is `[PROPOSED]`: `{ stage, percent, message, job_status }`. The stream polls
the `jobs` row (the durable mirror of Redis progress written by the worker),
closes on a terminal status, and sends a heartbeat comment so proxies do not
drop the connection while a stage is still in progress.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.api.v1.access import assert_project_access
from app.api.v1.auth import get_current_user
from app.db.session import SessionLocal, get_db
from app.jobs.cancel import cancel_running_agent_runs
from app.jobs.queue import enqueue
from app.jobs.registry import get_handler
from app.jobs.state import IllegalJobTransition, guard_transition
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.user import User
from app.schemas.job import JobCreate, JobOut

logger = logging.getLogger("architectos.jobs.api")

router = APIRouter(prefix="/jobs", tags=["jobs"])

_TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
_POLL_INTERVAL_SECONDS = 0.3
_HEARTBEAT_INTERVAL_SECONDS = 15


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Job:
    # Members may only enqueue jobs for projects they can see.
    assert_project_access(db, current_user, payload.project_id)
    if get_handler(payload.type) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown job type: {payload.type}")
    return enqueue(db, payload.project_id, payload.type)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    assert_project_access(db, current_user, job.project_id)
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Job:
    """Cancel a queued or running job.

    A queued job is resolved immediately — nothing is running yet. A
    running job only gets `cancel_requested` set; the worker notices it at
    its next `report_progress` call and transitions the job itself (see
    `app.worker`). Either way, any `agent_runs` rows the job seeded that are
    still `running` are walked to `cancelled` here too, so the UI does not
    keep showing a run that will never finish.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    assert_project_access(db, current_user, job.project_id)

    if job.status == JobStatus.QUEUED:
        try:
            guard_transition(job.status, JobStatus.CANCELLED)
        except IllegalJobTransition:
            db.refresh(job)  # raced with the worker picking it up; fall through
        else:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            cancel_running_agent_runs(db, job.id)
            return job

    if job.status == JobStatus.RUNNING:
        job.cancel_requested = True
        db.commit()
        return job

    if job.status != JobStatus.CANCELLED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"cannot cancel a job in status '{job.status.value}'"
        )
    return job


def _sse_event(job: Job) -> str:
    if job.progress_json is not None:
        payload = dict(job.progress_json)
    else:
        # No progress was ever mirrored, so the job never started running.
        # The generic "waiting to start" is only truthful while it is queued
        # — a terminal row must not claim to still be waiting.
        payload = {
            "stage": job.status.value,
            "percent": 0,
            "message": {
                JobStatus.FAILED: "the job failed before it could start",
                JobStatus.CANCELLED: "the job was cancelled before it started",
            }.get(job.status, "waiting to start"),
        }
    payload["job_status"] = job.status.value
    if job.status == JobStatus.FAILED and job.error:
        payload["error"] = job.error
    return f"data: {json.dumps(payload)}\n\n"


async def _event_stream(job_id: int):
    last_event: str | None = None
    since_heartbeat = 0.0
    db = SessionLocal()
    try:
        while True:
            # End any open transaction so the next read starts a fresh one —
            # under MySQL's default REPEATABLE READ a still-open transaction
            # would keep returning its original snapshot and never observe
            # the worker's commits. `rollback()` also expires cached objects.
            db.rollback()
            job = db.get(Job, job_id)
            if job is None:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                return

            event = _sse_event(job)
            if event != last_event:
                yield event
                last_event = event
                since_heartbeat = 0.0

            if job.status in _TERMINAL_STATUSES:
                return

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            since_heartbeat += _POLL_INTERVAL_SECONDS
            if since_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
                yield ": heartbeat\n\n"
                since_heartbeat = 0.0
    finally:
        db.close()


@router.get("/{job_id}/events")
async def job_events(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    assert_project_access(db, current_user, job.project_id)
    return StreamingResponse(
        _event_stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
