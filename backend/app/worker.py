"""Worker entrypoint (step 1.C.2).

One process, blocking on `BRPOP` against the Redis job queue. Loads the job
row from MySQL, looks the handler up in the registry by `jobs.type`, and
runs it. An unhandled handler exception — or an unknown job type — ends the
job `failed` with an error payload; it never ends `succeeded`.

Cancellation is cooperative: `POST /api/v1/jobs/{id}/cancel` sets
`jobs.cancel_requested` (running jobs) or jumps straight to `cancelled`
(queued jobs, before the worker even sees them). The `report_progress`
callback handed to every handler is the only place a handler yields control
back to the worker mid-run, so it doubles as the cancellation checkpoint —
it commits progress, re-reads `cancel_requested` from that fresh snapshot,
and raises `JobCancelledError` to unwind the handler.

Run with: `python -m app.worker` from `backend/`.
"""

import logging
import time
from datetime import datetime, timezone

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, job_id_var
from app.db.session import SessionLocal
from app.jobs import handlers  # noqa: F401  (import side effect: registers handlers)
from app.jobs.cancel import (
    cancel_running_agent_runs,
    fail_running_agent_runs,
    reconcile_orphaned_agent_runs,
)
from app.jobs.queue import HEAVY_JOB_COUNTER_KEY, HEAVY_JOB_TYPES, get_redis, mirror_progress
from app.jobs.registry import get_handler
from app.jobs.state import guard_transition
from app.models.job import Job, JobStatus

logger = logging.getLogger("architectos.worker")

_TERMINAL_JOB_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}

# `jobs.error` / `agent_runs.error` are TEXT (MySQL: 65,535-byte cap). A
# handler exception's str() has no size bound of its own — e.g. a
# SnapshotError wrapping one failure per file in a large copytree — and an
# oversized value must not be allowed to turn "mark this job failed" into
# a second, unhandled failure.
_MAX_ERROR_CHARS = 20_000

# How long a worker waits after requeueing a heavy job that could not get a
# slot. The sleep keeps a free worker from hot-spinning pop -> refuse -> push
# while the heavy job that holds the slot is still running.
_HEAVY_SLOT_RETRY_SECONDS = 10.0


def _truncate_error(error: str) -> str:
    if len(error) <= _MAX_ERROR_CHARS:
        return error
    return error[:_MAX_ERROR_CHARS] + f"... [truncated, {len(error)} chars total]"


class JobCancelledError(Exception):
    """Raised from within `report_progress` once `cancel_requested` is seen."""


def _set_status(db: Session, job: Job, status: JobStatus, **fields: object) -> None:
    guard_transition(job.status, status)
    job.status = status
    for key, value in fields.items():
        setattr(job, key, value)
    db.commit()


def _try_acquire_heavy_slot(settings: Settings) -> bool:
    """Resource-manager thin hook (§6.2): refuse a second heavy job while one runs."""
    client = get_redis(settings)
    count = client.incr(HEAVY_JOB_COUNTER_KEY)
    if count == 1:
        client.expire(HEAVY_JOB_COUNTER_KEY, 3600)
    if count > settings.max_concurrent_heavy_jobs:
        client.decr(HEAVY_JOB_COUNTER_KEY)
        return False
    return True


def _release_heavy_slot(settings: Settings) -> None:
    client = get_redis(settings)
    count = client.decr(HEAVY_JOB_COUNTER_KEY)
    if count < 0:
        client.set(HEAVY_JOB_COUNTER_KEY, 0)


def _running_heavy_jobs(db: Session, *, excluding_job_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.type.in_(HEAVY_JOB_TYPES),
                Job.id != excluding_job_id,
            )
        )
        or 0
    )


def _fail_job_without_running(
    db: Session, job: Job, error: str
) -> None:
    """Terminal-fail a job that never entered `running`, plus its seed runs."""
    error = _truncate_error(error)
    fail_running_agent_runs(db, job.id, error=error)
    _set_status(db, job, JobStatus.FAILED, error=error, finished_at=datetime.now(timezone.utc))


def run_job(db: Session, job_id: int, settings: Settings) -> None:
    """Load `job_id`, dispatch it to its handler, and drive it to a terminal status."""
    token = job_id_var.set(str(job_id))
    try:
        job = db.get(Job, job_id)
        if job is None:
            logger.error("job not found (job_id=%s)", job_id)
            return

        if job.status in _TERMINAL_JOB_STATUSES:
            # Cancelled (or otherwise resolved) while still queued, before we
            # popped it off Redis — nothing to run.
            logger.info("job already %s, skipping (job_id=%s)", job.status.value, job.id)
            return

        handler = get_handler(job.type)
        if handler is None:
            logger.error("unknown job type (job_id=%s, type=%s)", job.id, job.type)
            _fail_job_without_running(db, job, f"unknown job type: {job.type}")
            return

        heavy = job.type in HEAVY_JOB_TYPES
        if heavy and not _try_acquire_heavy_slot(settings):
            # Redis can stay incremented after a worker crash even when no
            # heavy job is actually `running`. Trust MySQL and retry once.
            if _running_heavy_jobs(db, excluding_job_id=job.id) == 0:
                get_redis(settings).set(HEAVY_JOB_COUNTER_KEY, 0)
            if not _try_acquire_heavy_slot(settings):
                # Another worker holds the heavy slot. Do NOT fail this job —
                # a refused-then-failed job strands the UI on a dead "failed /
                # waiting to start" panel with nothing left to stop. Requeue it
                # at the back of the queue instead: the row stays `queued`
                # (still cancellable from the UI) and starts automatically once
                # the slot frees up.
                logger.info("heavy slot busy; requeueing (job_id=%s, type=%s)", job.id, job.type)
                mirror_progress(
                    db,
                    job,
                    stage="queued",
                    percent=0,
                    message="waiting for another heavy job to finish; this run starts automatically",
                    settings=settings,
                )
                get_redis(settings).rpush(settings.job_queue_key, str(job.id))
                time.sleep(_HEAVY_SLOT_RETRY_SECONDS)
                return

        try:
            _set_status(db, job, JobStatus.RUNNING, started_at=datetime.now(timezone.utc))

            def report_progress(stage: str, percent: int, message: str) -> None:
                mirror_progress(db, job, stage=stage, percent=percent, message=message, settings=settings)
                # mirror_progress just committed, so this re-read starts a
                # fresh transaction and sees any cancel_requested set by
                # another session (the cancel endpoint) in the meantime.
                db.refresh(job)
                if job.cancel_requested:
                    raise JobCancelledError()

            try:
                handler(job, db, report_progress)
            except JobCancelledError:
                logger.info("job cancelled (job_id=%s, type=%s)", job.id, job.type)
                db.rollback()
                job = db.get(Job, job_id)
                _set_status(db, job, JobStatus.CANCELLED, finished_at=datetime.now(timezone.utc))
                cancel_running_agent_runs(db, job.id)
                return
            except Exception as exc:  # noqa: BLE001 - a handler failure must become `failed`, not crash the worker
                logger.exception("job handler failed (job_id=%s, type=%s)", job.id, job.type)
                try:
                    db.rollback()
                except Exception:
                    logger.exception("rollback after handler failure failed (job_id=%s)", job_id)
                job = db.get(Job, job_id)
                if job is None:
                    return
                error_detail = _truncate_error(str(exc))
                try:
                    fail_running_agent_runs(db, job.id, error=error_detail)
                except Exception:
                    # A failed commit here (e.g. the DataError this truncation
                    # now prevents) leaves `db` mid-transaction — logging or
                    # reusing `job` without rolling back first would just
                    # raise PendingRollbackError and lose the FAILED write below.
                    logger.exception(
                        "failed to mark agent runs failed (job_id=%s)", job_id
                    )
                    try:
                        db.rollback()
                    except Exception:
                        logger.exception(
                            "rollback after agent-run failure-marking failed (job_id=%s)", job_id
                        )
                    job = db.get(Job, job_id)
                    if job is None:
                        return
                try:
                    _set_status(
                        db,
                        job,
                        JobStatus.FAILED,
                        error=error_detail,
                        finished_at=datetime.now(timezone.utc),
                    )
                except Exception:
                    logger.exception("failed to mark job failed (job_id=%s)", job_id)
                return

            _set_status(db, job, JobStatus.SUCCEEDED, finished_at=datetime.now(timezone.utc))
        finally:
            if heavy:
                _release_heavy_slot(settings)
    finally:
        job_id_var.reset(token)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    redis_client = get_redis(settings)
    # One worker process: a previous crash can leave the heavy-job counter
    # > 0, after which every code_change/audit/agent_run is refused.
    redis_client.set(HEAVY_JOB_COUNTER_KEY, 0)
    startup_db = SessionLocal()
    try:
        orphaned = reconcile_orphaned_agent_runs(startup_db)
        if orphaned:
            logger.info("reconciled %s orphaned running agent runs", orphaned)
    finally:
        startup_db.close()
    logger.info("worker started, listening on '%s'", settings.job_queue_key)

    while True:
        try:
            item = redis_client.brpop([settings.job_queue_key], timeout=5)
        except redis.exceptions.TimeoutError:
            # redis-py on Windows can raise on a blocking pop that simply
            # expired, instead of returning None.
            continue
        except redis.exceptions.ConnectionError as exc:
            logger.warning("redis connection lost: %s", exc)
            time.sleep(1)
            continue
        if item is None:
            try:
                from app.services.site_report_schedule import tick_due_schedules

                tick_due_schedules(settings)
            except Exception:
                logger.exception("scheduled site_report tick failed")
            continue
        _, raw_job_id = item
        db = SessionLocal()
        try:
            run_job(db, int(raw_job_id), settings)
        except Exception:
            logger.exception(
                "run_job crashed (job_id=%s); worker continues listening",
                raw_job_id,
            )
        finally:
            db.close()


if __name__ == "__main__":
    main()
