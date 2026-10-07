"""GitHub OAuth + PAT connection API. Tokens never appear in responses."""

import uuid
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from app.connectors.github.oauth import OAUTH_COOKIE_NAME, build_authorization_url, pack_oauth_cookie
from app.core.config import Settings, get_settings
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
        "/api/v1/projects", json={"name": f"github-api-{uuid.uuid4()}"}
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


def test_put_pat_never_returns_the_token(auth_client, project) -> None:
    token = "ghp_this-must-not-leak"
    created = auth_client.put(
        f"/api/v1/projects/{project['id']}/github/connection",
        json={"pat": token},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["connected"] is True
    assert body["has_token"] is True
    assert body["auth_type"] == "pat"
    assert body["capabilities"]["pull_request"] == "high"
    dumped = created.text
    assert token not in dumped
    assert "credentials_encrypted" not in dumped
    assert "pat" not in body

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/github")
    assert fetched.status_code == 200
    assert token not in fetched.text
    assert fetched.json()["connection"]["has_token"] is True

    caps = auth_client.get(f"/api/v1/projects/{project['id']}/capabilities")
    assert caps.status_code == 200
    assert caps.json()["report"]["pull_request"] == "high"


def test_delete_pat_clears_connection(auth_client, project) -> None:
    auth_client.put(
        f"/api/v1/projects/{project['id']}/github/connection",
        json={"pat": "ghp_temp"},
    )
    deleted = auth_client.delete(f"/api/v1/projects/{project['id']}/github/connection")
    assert deleted.status_code == 200
    assert deleted.json()["has_token"] is False
    assert "ghp_temp" not in deleted.text


@pytest.fixture
def oauth_settings(monkeypatch):
    live = get_settings()
    settings = Settings(
        APP_SECRET_KEY=live.app_secret_key,
        CREDENTIAL_ENCRYPTION_KEY=live.credential_encryption_key or Fernet.generate_key().decode(),
        GITHUB_OAUTH_CLIENT_ID="ov_test_client",
        GITHUB_OAUTH_CLIENT_SECRET="github-oauth-client-secret",
        GITHUB_OAUTH_REDIRECT_URI="http://testserver/api/v1/github/oauth/callback",
        FRONTEND_ORIGIN=live.frontend_origin,
        APP_ENV="development",
        SEED_USER_EMAIL=live.seed_user_email,
        SEED_USER_PASSWORD=live.seed_user_password,
    )
    monkeypatch.setattr("app.api.v1.github.get_settings", lambda: settings)
    monkeypatch.setattr("app.connectors.github.oauth.get_settings", lambda: settings)
    return settings


def test_oauth_start_without_config_is_400(auth_client, project, monkeypatch) -> None:
    live = get_settings()
    empty = Settings(
        APP_SECRET_KEY=live.app_secret_key,
        CREDENTIAL_ENCRYPTION_KEY=live.credential_encryption_key or Fernet.generate_key().decode(),
        GITHUB_OAUTH_CLIENT_ID="",
        GITHUB_OAUTH_CLIENT_SECRET="",
    )
    monkeypatch.setattr("app.api.v1.github.get_settings", lambda: empty)
    monkeypatch.setattr("app.connectors.github.oauth.get_settings", lambda: empty)
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/github/oauth/start",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "OAuth" in resp.json()["detail"]


def test_oauth_start_redirects_to_github(auth_client, project, oauth_settings) -> None:
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/github/oauth/start",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "github-oauth-client-secret" not in location
    assert OAUTH_COOKIE_NAME in resp.cookies


def test_oauth_callback_stores_access_token_and_never_returns_it(
    auth_client, project, oauth_settings, monkeypatch
) -> None:
    _url, state, verifier, redirect_uri = build_authorization_url(
        project["id"], settings=oauth_settings
    )
    packed = pack_oauth_cookie(
        project["id"], verifier, state, oauth_settings, redirect_uri=redirect_uri
    )
    auth_client.cookies.set(OAUTH_COOKIE_NAME, packed)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.content = b"{}"
    token_response.text = ""
    token_response.json.return_value = {
        "access_token": "gho_must-not-leak",
        "refresh_token": "ghr_refresh-must-not-leak",
        "token_type": "bearer",
        "scope": "repo,read:user",
    }

    fake = MagicMock()
    fake.post.return_value = token_response
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = None
    monkeypatch.setattr("app.api.v1.github.httpx.Client", lambda **_kwargs: fake)

    class _FakeRest:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_authenticated_user(self):
            return {"login": "octocat"}

    monkeypatch.setattr("app.api.v1.github.GitHubRest", lambda token, **_kwargs: _FakeRest())

    resp = auth_client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert "github=connected" in resp.headers["location"]
    assert "gho_must-not-leak" not in resp.headers["location"]
    assert "ghr_refresh-must-not-leak" not in resp.text
    assert "gho_must-not-leak" not in resp.text

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/github")
    assert fetched.status_code == 200
    body = fetched.json()["connection"]
    assert body["has_token"] is True
    assert body["auth_type"] == "oauth2"
    assert body["login"] == "octocat"
    assert "gho_must-not-leak" not in fetched.text
    assert "credentials_encrypted" not in fetched.text

    caps = auth_client.get(f"/api/v1/projects/{project['id']}/capabilities")
    assert caps.status_code == 200
    assert caps.json()["report"]["pull_request"] == "high"


def test_put_repo_without_oauth_is_rejected(auth_client, project) -> None:
    created = auth_client.put(
        f"/api/v1/projects/{project['id']}/github/connection",
        json={"full_name": "acme/shop"},
    )
    assert created.status_code == 400
    assert "not connected" in created.json()["detail"].lower()


def test_list_repos_without_connection_is_409(auth_client, project) -> None:
    resp = auth_client.get(f"/api/v1/projects/{project['id']}/github/repositories")
    assert resp.status_code == 409


def test_select_repo_attaches_repository(auth_client, project, monkeypatch) -> None:
    token = "ghp_select-repo"
    stored = auth_client.put(
        f"/api/v1/projects/{project['id']}/github/connection",
        json={"pat": token},
    )
    assert stored.status_code == 200

    class _FakeRest:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_repository(self, owner, repo):
            return {
                "full_name": f"{owner}/{repo}",
                "clone_url": f"https://github.com/{owner}/{repo}.git",
                "default_branch": "main",
                "private": True,
                "permissions": {"push": True, "admin": False},
            }

        def list_user_repositories(self, **_kwargs):
            return [self.get_repository("acme", "shop")]

    monkeypatch.setattr("app.api.v1.github.GitHubRest", lambda tok, **_kwargs: _FakeRest())

    listed = auth_client.get(f"/api/v1/projects/{project['id']}/github/repositories")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["full_name"] == "acme/shop"
    assert token not in listed.text

    selected = auth_client.put(
        f"/api/v1/projects/{project['id']}/github/connection",
        json={"full_name": "acme/shop"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selected_repo"] == "acme/shop"
    assert token not in selected.text

    repo = auth_client.get(f"/api/v1/projects/{project['id']}/repository")
    assert repo.status_code == 200
    assert repo.json()["url"] == "https://github.com/acme/shop.git"
