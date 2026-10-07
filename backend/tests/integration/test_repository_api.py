"""Attach/get git repository for a project (step 2.B.1)."""

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
    resp = auth_client.post("/api/v1/projects", json={"name": "repo-attach-test"})
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


def test_attach_and_get_repository_does_not_return_token(auth_client, project) -> None:
    created = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={
            "url": "https://github.com/example/architectos-fixture.git",
            "default_branch": "main",
            "clone_token": "super-secret-token",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["url"] == "https://github.com/example/architectos-fixture.git"
    assert body["clone_status"] == "pending"
    assert "clone_token" not in body
    assert "clone_token_encrypted" not in body

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/repository")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert "clone_token" not in fetched.json()


def test_update_repository_token_sets_and_clears_without_returning_it(auth_client, project) -> None:
    created = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/private-repo.git"},
    )
    assert created.status_code == 201
    assert created.json()["has_clone_token"] is False

    set_token = auth_client.patch(
        f"/api/v1/projects/{project['id']}/repository/token",
        json={"clone_token": "super-secret-token"},
    )
    assert set_token.status_code == 200
    body = set_token.json()
    assert body["has_clone_token"] is True
    assert "clone_token" not in body
    assert "clone_token_encrypted" not in body

    cleared = auth_client.patch(
        f"/api/v1/projects/{project['id']}/repository/token",
        json={"clone_token": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_clone_token"] is False


def test_second_repository_on_same_project_is_conflict(auth_client, project) -> None:
    first = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/one.git"},
    )
    assert first.status_code == 201
    second = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/two.git"},
    )
    assert second.status_code == 409
