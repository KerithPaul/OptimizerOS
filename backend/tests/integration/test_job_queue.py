"""Enqueue -> worker -> terminal status, and the deliberate-failure path
(step 1.C.2/1.C.3 verify; Phase 1 "tests to write": Queue, Failure).

Runs against the real MySQL/Redis dev containers (docker compose), the same
way the rest of Phase 1 is verified. Every row created is cleaned up.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.jobs import handlers  # noqa: F401  (import side effect: registers handlers)
from app.jobs.queue import enqueue, get_redis
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.user import User
from app.worker import run_job


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db) -> Project:
    user = db.scalar(select(User).limit(1))
    if user is None:
        user = User(email=f"job-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)

    proj = Project(name=f"job-test-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj

    db.delete(proj)
    db.commit()


def _cleanup_job(db, job: Job) -> None:
    row = db.get(Job, job.id)
    if row is not None:
        db.delete(row)
        db.commit()


def test_health_ping_round_trips_to_succeeded(db, project) -> None:
    settings = get_settings()
    job = enqueue(db, project.id, "health_ping", settings)
    try:
        assert job.status == JobStatus.QUEUED

        raw_job_id = get_redis(settings).brpop([settings.job_queue_key], timeout=5)
        assert raw_job_id is not None
        _, popped_id = raw_job_id
        assert int(popped_id) == job.id

        worker_db = SessionLocal()
        try:
            run_job(worker_db, job.id, settings)
        finally:
            worker_db.close()

        db.rollback()
        finished = db.get(Job, job.id)
        assert finished.status == JobStatus.SUCCEEDED
        assert finished.progress_json["percent"] == 100
        assert finished.started_at is not None
        assert finished.finished_at is not None
    finally:
        _cleanup_job(db, job)


def test_health_ping_fail_ends_failed_never_succeeded(db, project) -> None:
    settings = get_settings()
    job = enqueue(db, project.id, "health_ping_fail", settings)
    try:
        get_redis(settings).brpop([settings.job_queue_key], timeout=5)

        worker_db = SessionLocal()
        try:
            run_job(worker_db, job.id, settings)
        finally:
            worker_db.close()

        db.rollback()
        finished = db.get(Job, job.id)
        assert finished.status == JobStatus.FAILED
        assert finished.status != JobStatus.SUCCEEDED
        assert "deliberate failure" in finished.error
    finally:
        _cleanup_job(db, job)


def test_handler_failure_still_ends_failed_when_marking_agent_runs_also_fails(
    db, project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second failure while recording the first (e.g. the DataError an
    oversized `error` column value used to cause) must not leave the job
    stuck `running` forever -- it must still reach `failed`."""
    import app.worker as worker_module

    def _boom(*args, **kwargs):
        raise RuntimeError("secondary failure while marking agent runs")

    monkeypatch.setattr(worker_module, "fail_running_agent_runs", _boom)

    settings = get_settings()
    job = enqueue(db, project.id, "health_ping_fail", settings)
    try:
        get_redis(settings).brpop([settings.job_queue_key], timeout=5)

        worker_db = SessionLocal()
        try:
            run_job(worker_db, job.id, settings)
        finally:
            worker_db.close()

        db.rollback()
        finished = db.get(Job, job.id)
        assert finished.status == JobStatus.FAILED
        assert "deliberate failure" in finished.error
    finally:
        _cleanup_job(db, job)


def test_unknown_job_type_fails_explicitly(db, project) -> None:
    settings = get_settings()
    job = enqueue(db, project.id, "no_such_handler", settings)
    try:
        get_redis(settings).brpop([settings.job_queue_key], timeout=5)

        worker_db = SessionLocal()
        try:
            run_job(worker_db, job.id, settings)
        finally:
            worker_db.close()

        db.rollback()
        finished = db.get(Job, job.id)
        assert finished.status == JobStatus.FAILED
        assert "unknown job type" in finished.error
    finally:
        _cleanup_job(db, job)
