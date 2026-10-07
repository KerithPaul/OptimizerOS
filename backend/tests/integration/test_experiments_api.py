"""Experiments API (step 11.4 / 11.5). Result never claims causation."""

import uuid

import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
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
def project(auth_client):
    resp = auth_client.post(
        "/api/v1/projects", json={"name": f"experiments-api-{uuid.uuid4()}"}
    )
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


def test_create_and_measure_does_not_claim_causation(auth_client, project) -> None:
    created = auth_client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        json={
            "hypothesis": "A more specific title will be associated with a higher CTR.",
            "experiment_type": "title",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["causation"] == "not_claimed"
    assert "correlation" in body["causation_note"].lower()
    assert "not evidence" in body["causation_note"].lower()
    assert body["baseline_metrics"]["causation"] == "not_claimed"
    assert body["baseline_metrics"]["metrics"]["impressions"]["status"] == "unavailable"
    assert body["baseline_metrics"]["metrics"]["answer_retrieval_benchmark"]["status"] == "unavailable"

    measured = auth_client.post(
        f"/api/v1/projects/{project['id']}/experiments/{body['id']}/measure-treatment"
    )
    assert measured.status_code == 200, measured.text
    result = measured.json()
    assert result["status"] == "result"
    assert result["causation"] == "not_claimed"
    assert result["result"]["causation"] == "not_claimed"
    assert result["result"]["causation_note"] == body["causation_note"]
    listed = auth_client.get(f"/api/v1/projects/{project['id']}/experiments")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]
