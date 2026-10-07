"""Search Console OAuth API. Refresh tokens never appear in responses."""

import uuid
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings as live_get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.project import Project
from app.services.search_console import (
    OAUTH_COOKIE_NAME,
    build_authorization_url,
    pack_oauth_cookie,
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    settings = live_get_settings()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": settings.seed_user_email, "password": settings.seed_user_password},
    )
    assert resp.status_code == 200
    return client


@pytest.fixture
def project(auth_client):
    resp = auth_client.post(
        "/api/v1/projects", json={"name": f"gsc-api-{uuid.uuid4()}"}
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


@pytest.fixture
def oauth_settings(monkeypatch):
    live = live_get_settings()
    settings = Settings(
        APP_SECRET_KEY=live.app_secret_key,
        CREDENTIAL_ENCRYPTION_KEY=live.credential_encryption_key or Fernet.generate_key().decode(),
        GSC_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com",
        GSC_OAUTH_CLIENT_SECRET="oauth-client-secret",
        GSC_OAUTH_REDIRECT_URI="http://testserver/api/v1/search-console/oauth/callback",
        FRONTEND_ORIGIN=live.frontend_origin,
        APP_ENV="development",
        SEED_USER_EMAIL=live.seed_user_email,
        SEED_USER_PASSWORD=live.seed_user_password,
    )
    monkeypatch.setattr("app.api.v1.search_console.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.search_console.get_settings", lambda: settings)
    return settings


def test_get_without_credentials_is_unavailable(auth_client, project) -> None:
    resp = auth_client.get(f"/api/v1/projects/{project['id']}/search-console")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connection"]["display"] == "Search Console: unavailable"
    assert body["connection"]["status"] == "unavailable"
    assert body["connection"]["has_stored_credentials"] is False
    assert "credentials_json" not in body["connection"]
    assert body["rows"] == []


def test_oauth_start_without_config_is_400(auth_client, project, monkeypatch) -> None:
    live = live_get_settings()
    empty = Settings(
        APP_SECRET_KEY=live.app_secret_key,
        CREDENTIAL_ENCRYPTION_KEY=live.credential_encryption_key or Fernet.generate_key().decode(),
        GSC_OAUTH_CLIENT_ID="",
        GSC_OAUTH_CLIENT_SECRET="",
    )
    monkeypatch.setattr("app.api.v1.search_console.get_settings", lambda: empty)
    monkeypatch.setattr("app.services.search_console.get_settings", lambda: empty)
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/search-console/oauth/start",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "OAuth" in resp.json()["detail"]


def test_oauth_start_redirects_to_google(auth_client, project, oauth_settings) -> None:
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/search-console/oauth/start",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in location
    assert "oauth-client-secret" not in location
    assert OAUTH_COOKIE_NAME in resp.cookies


def test_oauth_callback_stores_refresh_token_and_never_returns_it(
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
        "access_token": "ya29.access",
        "refresh_token": "1//refresh-must-not-leak",
    }
    sites_response = MagicMock()
    sites_response.status_code = 200
    sites_response.content = b"{}"
    sites_response.text = ""
    sites_response.json.return_value = {
        "siteEntry": [{"siteUrl": "https://example.com/", "permissionLevel": "siteFullUser"}]
    }

    fake = MagicMock()
    fake.post.return_value = token_response
    fake.get.return_value = sites_response
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = None
    monkeypatch.setattr("app.api.v1.search_console.httpx.Client", lambda **_kwargs: fake)

    resp = auth_client.get(
        "/api/v1/search-console/oauth/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    assert "gsc=connected" in resp.headers["location"]
    assert "1//refresh-must-not-leak" not in resp.headers["location"]
    assert "1//refresh-must-not-leak" not in resp.text
    assert "ya29.access" not in resp.text

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/search-console")
    assert fetched.status_code == 200
    body = fetched.json()["connection"]
    assert body["has_stored_credentials"] is True
    assert body["auth_type"] == "oauth2"
    assert body["property_url"] == "https://example.com/"
    assert body["properties"] == ["https://example.com/"]
    assert "1//refresh-must-not-leak" not in fetched.text
    assert "credentials_encrypted" not in fetched.text


def test_put_property_without_oauth_is_rejected(auth_client, project) -> None:
    created = auth_client.put(
        f"/api/v1/projects/{project['id']}/search-console/connection",
        json={"property_url": "https://example.com/"},
    )
    assert created.status_code == 400
    assert "OAuth" in created.json()["detail"]


def _complete_oauth(auth_client, project, oauth_settings, monkeypatch, sites: list[str]) -> None:
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
        "access_token": "ya29.access",
        "refresh_token": "1//refresh-must-not-leak",
    }
    sites_response = MagicMock()
    sites_response.status_code = 200
    sites_response.content = b"{}"
    sites_response.text = ""
    sites_response.json.return_value = {
        "siteEntry": [{"siteUrl": site} for site in sites]
    }
    fake = MagicMock()
    fake.post.return_value = token_response
    fake.get.return_value = sites_response
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = None
    monkeypatch.setattr("app.api.v1.search_console.httpx.Client", lambda **_kwargs: fake)
    resp = auth_client.get(
        "/api/v1/search-console/oauth/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text


def test_put_saves_selected_property_when_account_has_several(
    auth_client, project, oauth_settings, monkeypatch
) -> None:
    sites = ["https://example.com/", "sc-domain:drmoksha.com"]
    _complete_oauth(auth_client, project, oauth_settings, monkeypatch, sites)

    before = auth_client.get(f"/api/v1/projects/{project['id']}/search-console")
    assert before.status_code == 200
    assert before.json()["connection"]["property_url"] is None
    assert before.json()["connection"]["properties"] == sites
    assert before.json()["rows"] == []

    saved = auth_client.put(
        f"/api/v1/projects/{project['id']}/search-console/connection",
        json={"property_url": "sc-domain:drmoksha.com"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["property_url"] == "sc-domain:drmoksha.com"
    assert saved.json()["has_stored_credentials"] is True
    assert "1//refresh-must-not-leak" not in saved.text

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/search-console")
    assert fetched.status_code == 200
    assert fetched.json()["connection"]["property_url"] == "sc-domain:drmoksha.com"
    assert fetched.json()["rows"] == []


def test_delete_clears_connection(auth_client, project, oauth_settings, monkeypatch) -> None:
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
        "access_token": "at",
        "refresh_token": "1//refresh-delete-test",
    }
    sites_response = MagicMock()
    sites_response.status_code = 200
    sites_response.content = b"{}"
    sites_response.text = ""
    sites_response.json.return_value = {"siteEntry": [{"siteUrl": "https://example.com/"}]}
    fake = MagicMock()
    fake.post.return_value = token_response
    fake.get.return_value = sites_response
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = None
    monkeypatch.setattr("app.api.v1.search_console.httpx.Client", lambda **_kwargs: fake)
    auth_client.get(
        "/api/v1/search-console/oauth/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    deleted = auth_client.delete(
        f"/api/v1/projects/{project['id']}/search-console/connection"
    )
    assert deleted.status_code == 200
    assert deleted.json()["has_stored_credentials"] is False
    assert deleted.json()["display"] == "Search Console: unavailable"
    assert "1//" not in deleted.text
    assert deleted.json()["has_stored_credentials"] is False


def test_get_returns_page_and_query_rows_not_only_latest_page_query(
    auth_client, project
) -> None:
    from datetime import date, datetime, timezone

    from app.models.search import SearchConsoleDimension, SearchConsoleRow

    db = SessionLocal()
    try:
        common = dict(
            project_id=project["id"],
            analysis_run_id=None,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 28),
            fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            clicks=1,
            ctr=0.01,
            position=8.0,
        )
        db.add(
            SearchConsoleRow(
                **common,
                dimension=SearchConsoleDimension.PAGE,
                page="https://example.com/",
                impressions=500,
            )
        )
        db.add(
            SearchConsoleRow(
                **common,
                dimension=SearchConsoleDimension.QUERY,
                query="example query",
                impressions=400,
            )
        )
        db.flush()
        for index in range(220):
            db.add(
                SearchConsoleRow(
                    **common,
                    dimension=SearchConsoleDimension.PAGE_QUERY,
                    page="https://example.com/post",
                    query=f"q-{index}",
                    impressions=index,
                )
            )
        db.commit()
    finally:
        db.close()

    resp = auth_client.get(f"/api/v1/projects/{project['id']}/search-console")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    dims = {row["dimension"] for row in rows}
    assert "page" in dims
    assert "query" in dims
    assert "page_query" not in dims
    assert any(row["page"] == "https://example.com/" for row in rows)
    assert any(row["query"] == "example query" for row in rows)
