"""Project deletion endpoint (DELETE /api/v1/projects/{id})."""

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


def test_delete_project_returns_204_and_removes_row(auth_client) -> None:
    resp = auth_client.post("/api/v1/projects", json={"name": "delete-me-project"})
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    delete_resp = auth_client.delete(f"/api/v1/projects/{project_id}")
    assert delete_resp.status_code == 204

    get_resp = auth_client.get(f"/api/v1/projects/{project_id}")
    assert get_resp.status_code == 404

    db = SessionLocal()
    try:
        assert db.get(Project, project_id) is None
    finally:
        db.close()


def test_delete_missing_project_returns_404(auth_client) -> None:
    resp = auth_client.delete("/api/v1/projects/99999999")
    assert resp.status_code == 404


def test_delete_project_requires_auth(client) -> None:
    resp = client.delete("/api/v1/projects/1")
    assert resp.status_code in (401, 403)
