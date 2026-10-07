"""Agent-run API: listing and fetching persisted results after reload.

The dashboard history used to show only name/status; the stored
`result_json` (suggested interventions) is what the detail page needs.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.agent import AgentRun, AgentRunStatus, AgentType
from app.models.job import Job, JobStatus
from app.models.project import Project


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
def project_row(auth_client):
    resp = auth_client.post(
        "/api/v1/projects",
        json={"name": f"agents-api-test-{uuid.uuid4()}", "mode": "AUDIT_ONLY"},
    )
    assert resp.status_code == 201
    body = resp.json()
    yield body

    db = SessionLocal()
    try:
        for row in db.scalars(select(AgentRun).where(AgentRun.project_id == body["id"])):
            db.delete(row)
        for row in db.scalars(select(Job).where(Job.project_id == body["id"])):
            db.delete(row)
        db.commit()
        proj = db.get(Project, body["id"])
        if proj is not None:
            db.delete(proj)
            db.commit()
    finally:
        db.close()


def _seed_pipeline(project_id: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        job = Job(project_id=project_id, type="agent_run", status=JobStatus.SUCCEEDED)
        db.add(job)
        db.commit()
        db.refresh(job)

        research = AgentRun(
            project_id=project_id,
            job_id=job.id,
            agent_type=AgentType.RESEARCH,
            status=AgentRunStatus.SUCCEEDED,
            request_text="Improve product-page SEO",
            result_json={"knowledge": [{"locator": "SEO-CANONICAL-001"}], "gaps": []},
        )
        seo = AgentRun(
            project_id=project_id,
            job_id=job.id,
            agent_type=AgentType.SEO,
            status=AgentRunStatus.SUCCEEDED,
            request_text="Improve product-page SEO",
            result_json=[
                {
                    "finding_id": "SEO-CANONICAL-001:abc",
                    "hypothesis": "Missing canonical",
                    "intervention": "Add a canonical link tag.",
                    "expected_mechanism": "Deduplicates URLs.",
                    "risk": "low",
                }
            ],
        )
        db.add_all([research, seo])
        db.commit()
        db.refresh(seo)
        return job.id, seo.id
    finally:
        db.close()


def test_list_runs_includes_persisted_interventions(auth_client, project_row) -> None:
    _seed_pipeline(project_row["id"])
    resp = auth_client.get(f"/api/v1/projects/{project_row['id']}/agents/runs")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    seo = next(row for row in rows if row["agent_type"] == "seo")
    assert seo["result_json"][0]["finding_id"] == "SEO-CANONICAL-001:abc"
    assert seo["result_json"][0]["intervention"] == "Add a canonical link tag."


def test_get_run_returns_result_json(auth_client, project_row) -> None:
    job_id, seo_id = _seed_pipeline(project_row["id"])
    resp = auth_client.get(f"/api/v1/projects/{project_row['id']}/agents/runs/{seo_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == seo_id
    assert body["job_id"] == job_id
    assert body["result_json"][0]["intervention"] == "Add a canonical link tag."


def test_get_run_unknown_is_404(auth_client, project_row) -> None:
    resp = auth_client.get(f"/api/v1/projects/{project_row['id']}/agents/runs/99999999")
    assert resp.status_code == 404


def test_get_runs_by_job_not_shadowed_by_run_id_route(auth_client, project_row) -> None:
    job_id, _seo_id = _seed_pipeline(project_row["id"])
    resp = auth_client.get(f"/api/v1/projects/{project_row['id']}/agents/runs/by-job/{job_id}")
    assert resp.status_code == 200
    types = {row["agent_type"] for row in resp.json()}
    assert types == {"research", "seo"}
