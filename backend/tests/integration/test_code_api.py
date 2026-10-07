"""Code-intelligence API (step 2.D.4)."""

import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.repository.ast import extract_repository
from app.intelligence.repository.filter import filter_repository
from app.intelligence.repository.graph import delete_repository_graph, write_graph
from app.main import app
from app.models.project import Project
from app.models.repository import Repository

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"


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
    resp = auth_client.post("/api/v1/projects", json={"name": "code-api-test"})
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


def test_files_symbols_routes_and_neighbors(auth_client, project) -> None:
    attached = auth_client.post(
        f"/api/v1/projects/{project['id']}/repository",
        json={"url": "https://github.com/example/golden-a.git"},
    )
    assert attached.status_code == 201
    repository_id = attached.json()["id"]

    filtered = filter_repository(_GOLDEN_A)
    extracted = extract_repository(_GOLDEN_A, filtered.included_paths)
    write_graph(project["id"], repository_id, extracted, commit_hash="test")
    try:
        files = auth_client.get(f"/api/v1/projects/{project['id']}/repository/files")
        assert files.status_code == 200
        paths = {row["path"] for row in files.json()}
        assert "lib/product-service.ts" in paths

        symbols = auth_client.get(f"/api/v1/projects/{project['id']}/repository/symbols")
        assert symbols.status_code == 200
        names = {row["name"] for row in symbols.json()}
        assert "ProductPage" in names
        assert "getProduct" in names

        routes = auth_client.get(f"/api/v1/projects/{project['id']}/repository/routes")
        assert routes.status_code == 200
        route_paths = {row["path"] for row in routes.json()}
        assert "/products/[slug]" in route_paths

        neighbors = auth_client.get(
            f"/api/v1/projects/{project['id']}/repository/neighbors",
            params={"name": "getProduct", "relation": "calls"},
        )
        assert neighbors.status_code == 200
        caller_names = {row["name"] for row in neighbors.json()}
        assert "ProductPage" in caller_names

        detail = auth_client.get(f"/api/v1/projects/{project['id']}/repository")
        assert detail.status_code == 200
        assert detail.json()["indexing_state"] in {"pending", "cloned", "indexed"}
    finally:
        delete_repository_graph(project["id"], repository_id)
