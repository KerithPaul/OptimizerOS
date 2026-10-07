"""SSE endpoint end-to-end (step 1.C.4 verify): the stream ends with the
job's terminal status and does not hang.

Runs against the real MySQL/Redis dev containers. A background thread plays
the worker's role for a single job so the test does not depend on a
separately-running `python -m app.worker` process.
"""

import json
import threading

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.jobs.queue import get_redis
from app.main import app
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.user import User
from app.worker import run_job


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    settings = get_settings()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": settings.seed_user_email, "password": settings.seed_user_password},
    )
    assert resp.status_code == 200
    return client


@pytest.fixture
def project(auth_client):
    resp = auth_client.post("/api/v1/projects", json={"name": "sse-test-project"})
    assert resp.status_code == 201
    body = resp.json()
    yield body

    db = SessionLocal()
    try:
        row = db.get(Project, body["id"])
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def _run_one_job_in_background(job_id: int) -> None:
    settings = get_settings()

    def _worker() -> None:
        popped = get_redis(settings).brpop([settings.job_queue_key], timeout=5)
        assert popped is not None
        db = SessionLocal()
        try:
            run_job(db, job_id, settings)
        finally:
            db.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def test_sse_stream_ends_with_terminal_status(auth_client, project) -> None:
    resp = auth_client.post("/api/v1/jobs", json={"project_id": project["id"], "type": "health_ping"})
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    thread = _run_one_job_in_background(job_id)
    try:
        statuses_seen = []
        with auth_client.stream("GET", f"/api/v1/jobs/{job_id}/events", timeout=15) as stream:
            for line in stream.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                statuses_seen.append(event["job_status"])
                if event["job_status"] in {"succeeded", "failed", "cancelled"}:
                    break

        assert statuses_seen[-1] == "succeeded"
    finally:
        thread.join(timeout=5)

    db = SessionLocal()
    try:
        db.delete(db.get(Job, job_id))
        db.commit()
    finally:
        db.close()


def test_sse_stream_reports_failure(auth_client, project) -> None:
    resp = auth_client.post(
        "/api/v1/jobs", json={"project_id": project["id"], "type": "health_ping_fail"}
    )
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    thread = _run_one_job_in_background(job_id)
    try:
        final_event = None
        with auth_client.stream("GET", f"/api/v1/jobs/{job_id}/events", timeout=15) as stream:
            for line in stream.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                if event["job_status"] in {"succeeded", "failed", "cancelled"}:
                    final_event = event
                    break

        assert final_event["job_status"] == "failed"
        assert "error" in final_event
    finally:
        thread.join(timeout=5)

    db = SessionLocal()
    try:
        db.delete(db.get(Job, job_id))
        db.commit()
    finally:
        db.close()


def test_cancel_queued_job_resolves_immediately(auth_client, project) -> None:
    resp = auth_client.post("/api/v1/jobs", json={"project_id": project["id"], "type": "health_ping"})
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    resp = auth_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        assert job.status == JobStatus.CANCELLED
        assert job.finished_at is not None
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_cancel_running_job_sets_cancel_requested(auth_client, project) -> None:
    resp = auth_client.post("/api/v1/jobs", json={"project_id": project["id"], "type": "health_ping"})
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    # Drain the queue push so the worker never picks this one up, then move
    # it to `running` by hand to simulate the worker already having claimed it.
    get_redis(get_settings()).brpop([get_settings().job_queue_key], timeout=5)
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        job.status = JobStatus.RUNNING
        db.commit()
    finally:
        db.close()

    resp = auth_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["cancel_requested"] is True

    db = SessionLocal()
    try:
        db.delete(db.get(Job, job_id))
        db.commit()
    finally:
        db.close()


def test_cancel_terminal_job_is_rejected(auth_client, project) -> None:
    resp = auth_client.post("/api/v1/jobs", json={"project_id": project["id"], "type": "health_ping"})
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    thread = _run_one_job_in_background(job_id)
    thread.join(timeout=5)

    resp = auth_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 400

    db = SessionLocal()
    try:
        db.delete(db.get(Job, job_id))
        db.commit()
    finally:
        db.close()


def test_create_job_accepts_site_report_type(auth_client, project) -> None:
    resp = auth_client.post(
        "/api/v1/jobs", json={"project_id": project["id"], "type": "site_report"}
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "site_report"
    db = SessionLocal()
    try:
        db.delete(db.get(Job, resp.json()["id"]))
        db.commit()
    finally:
        db.close()


def test_create_job_rejects_unknown_type(auth_client, project) -> None:
    resp = auth_client.post(
        "/api/v1/jobs", json={"project_id": project["id"], "type": "no_such_handler"}
    )
    assert resp.status_code == 400


def test_create_job_rejects_unknown_project(auth_client) -> None:
    resp = auth_client.post("/api/v1/jobs", json={"project_id": 0, "type": "health_ping"})
    assert resp.status_code == 404
