"""Website attach + capability-backed mode validator (steps 3.A.3–3.A.4)."""

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
    resp = auth_client.post("/api/v1/projects", json={"name": "website-attach-test"})
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


def test_attach_url_only_website_persists_capability_report(auth_client, project) -> None:
    created = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/", "platform": "url_only"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["url"] == "https://example.com/"
    assert body["platform"] == "url_only"
    assert body["auth_type"] == "none"
    assert body["capabilities"]["seo_modification"] == "none"
    assert body["capabilities"]["snapshot"] is False
    assert "credentials_encrypted" not in body

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/website")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    caps = auth_client.get(f"/api/v1/projects/{project['id']}/capabilities")
    assert caps.status_code == 200
    assert caps.json()["allowed_modes"] == ["AUDIT_ONLY", "SUGGEST_ONLY"]
    assert caps.json()["report"]["platform"] == "url_only"


def test_url_only_project_cannot_be_raised_to_apply_locally(auth_client, project) -> None:
    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 201, attached.text

    refused = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "APPLY_LOCALLY" in detail
    assert "SUGGEST_ONLY" in detail

    allowed = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "SUGGEST_ONLY"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["mode"] == "SUGGEST_ONLY"


def test_project_without_website_can_still_set_apply_locally(auth_client, project) -> None:
    caps = auth_client.get(f"/api/v1/projects/{project['id']}/capabilities")
    assert caps.status_code == 200
    assert caps.json()["report"] is None
    assert "APPLY_LOCALLY" in caps.json()["allowed_modes"]

    resp = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "APPLY_LOCALLY"


def test_cannot_attach_url_only_while_mode_is_apply_locally(auth_client, project) -> None:
    raised = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert raised.status_code == 200
    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 409
    assert "APPLY_LOCALLY" in attached.json()["detail"]


def test_git_plus_url_only_can_raise_to_apply_locally(auth_client, project) -> None:
    repo = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/architectos-fixture.git"},
    )
    assert repo.status_code == 201, repo.text

    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 201, attached.text
    assert attached.json()["capabilities"]["seo_modification"] == "none"

    caps = auth_client.get(f"/api/v1/projects/{project['id']}/capabilities")
    assert caps.status_code == 200
    body = caps.json()
    assert "APPLY_LOCALLY" in body["allowed_modes"]
    assert "COMMIT" in body["allowed_modes"]
    assert "CREATE_PR" not in body["allowed_modes"]
    assert body["report"]["platform"] == "git+url_only"
    assert body["report"]["source_access"] is True

    raised = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["mode"] == "APPLY_LOCALLY"


def test_attaching_git_after_url_only_unlocks_apply_locally(auth_client, project) -> None:
    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 201, attached.text
    refused = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert refused.status_code == 409

    repo = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/architectos-fixture.git"},
    )
    assert repo.status_code == 201, repo.text
    raised = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["mode"] == "APPLY_LOCALLY"


def test_apply_locally_with_git_can_attach_url_only_website(auth_client, project) -> None:
    repo = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/architectos-fixture.git"},
    )
    assert repo.status_code == 201, repo.text
    raised = auth_client.patch(
        f"/api/v1/projects/{project['id']}/mode",
        json={"mode": "APPLY_LOCALLY"},
    )
    assert raised.status_code == 200
    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 201, attached.text
    mode = auth_client.get(f"/api/v1/projects/{project['id']}")
    assert mode.json()["mode"] == "APPLY_LOCALLY"


def test_second_website_on_same_project_is_conflict(auth_client, project) -> None:
    first = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert first.status_code == 201
    second = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://other.example/"},
    )
    assert second.status_code == 409


def test_pages_are_empty_before_a_crawl(auth_client, project) -> None:
    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 201
    pages = auth_client.get(f"/api/v1/projects/{project['id']}/website/pages")
    assert pages.status_code == 200
    assert pages.json() == []
    detail = auth_client.get(f"/api/v1/projects/{project['id']}/website")
    assert detail.status_code == 200
    assert detail.json()["latest_crawl"] is None


def test_start_crawl_enqueues_website_crawl_job(auth_client, project) -> None:
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.jobs.queue import get_redis
    from app.models.job import Job

    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/"},
    )
    assert attached.status_code == 201
    created = auth_client.post(f"/api/v1/projects/{project['id']}/website/crawl")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["type"] == "website_crawl"
    assert body["status"] == "queued"
    settings = get_settings()
    get_redis(settings).brpop([settings.job_queue_key], timeout=2)
    db = SessionLocal()
    try:
        row = db.get(Job, body["id"])
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def test_crawl_without_website_is_not_found(auth_client, project) -> None:
    resp = auth_client.post(f"/api/v1/projects/{project['id']}/website/crawl")
    assert resp.status_code == 404


def test_unknown_platform_is_rejected(auth_client, project) -> None:
    resp = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://example.com/", "platform": "wordpress"},
    )
    assert resp.status_code == 400
    assert "wordpress" in resp.json()["detail"]
