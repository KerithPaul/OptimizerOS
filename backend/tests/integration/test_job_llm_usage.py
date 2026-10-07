"""LLM call accounting persists on the jobs row (step 2.A.4).

Runs against the real MySQL dev container. Every row created is cleaned up.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.llm.gateway import LLMCallRecord, record_llm_call
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.user import User


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
        user = User(email=f"llm-acct-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)

    proj = Project(name=f"llm-acct-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj

    db.delete(proj)
    db.commit()


def test_llm_step_records_nonzero_token_usage_on_the_jobs_row(db, project) -> None:
    job = Job(project_id=project.id, type="health_ping", status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        record_llm_call(
            db,
            job,
            LLMCallRecord(
                provider="groq",
                model="test-small",
                tokens=37,
                latency_ms=12,
            ),
        )

        other = SessionLocal()
        try:
            loaded = other.get(Job, job.id)
            assert loaded is not None
            assert loaded.llm_usage_json is not None
            assert loaded.llm_usage_json[0]["tokens"] == 37
            assert loaded.llm_usage_json[0]["tokens"] > 0
            assert loaded.llm_usage_json[0]["provider"] == "groq"
            assert loaded.llm_usage_json[0]["model"] == "test-small"
            assert loaded.llm_usage_json[0]["latency_ms"] == 12
        finally:
            other.close()
    finally:
        row = db.get(Job, job.id)
        if row is not None:
            db.delete(row)
            db.commit()
