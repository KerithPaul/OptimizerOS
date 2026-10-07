"""GSC OAuth 2.0 authorization URL and token exchange (no JSON key)."""

from cryptography.fernet import Fernet

from app.core.config import Settings
from app.services.search_console import (
    SearchConsoleError,
    build_authorization_url,
    exchange_authorization_code,
    oauth_configured,
)


def _settings(**overrides) -> Settings:
    values = dict(
        APP_SECRET_KEY="unit-test-secret",
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        GSC_OAUTH_CLIENT_ID="client.apps.googleusercontent.com",
        GSC_OAUTH_CLIENT_SECRET="oauth-secret",
        GSC_OAUTH_REDIRECT_URI="http://localhost:8000/api/v1/search-console/oauth/callback",
    )
    values.update(overrides)
    return Settings(**values)


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}" if payload is not None else b""
        self.text = text or ""

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.payload, self.status_code, text="error")


def test_oauth_configured_requires_client_id_and_secret() -> None:
    assert oauth_configured(_settings()) is True
    assert (
        oauth_configured(_settings(GSC_OAUTH_CLIENT_ID="", GSC_OAUTH_CLIENT_SECRET="x"))
        is False
    )


def test_authorization_url_is_google_oauth_with_offline_access_and_pkce() -> None:
    url, state, verifier, redirect_uri = build_authorization_url(42, settings=_settings())
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "code_challenge_method=S256" in url
    assert "webmasters.readonly" in url
    assert "client.apps.googleusercontent.com" in url
    assert redirect_uri.endswith("/search-console/oauth/callback")
    assert "redirect_uri=" in url
    assert verifier
    assert state
    assert "oauth-secret" not in url


def test_authorization_url_uses_request_derived_redirect_when_env_blank() -> None:
    url, _state, _verifier, redirect_uri = build_authorization_url(
        1,
        settings=_settings(GSC_OAUTH_REDIRECT_URI=""),
        redirect_uri="http://localhost:6001/api/v1/search-console/oauth/callback",
    )
    assert redirect_uri == "http://localhost:6001/api/v1/search-console/oauth/callback"
    assert "localhost%3A6001" in url or "localhost:6001" in url


def test_authorization_url_without_client_fails() -> None:
    try:
        build_authorization_url(1, settings=_settings(GSC_OAUTH_CLIENT_ID="", GSC_OAUTH_CLIENT_SECRET=""))
    except SearchConsoleError as exc:
        assert "OAuth is not configured" in str(exc)
    else:
        raise AssertionError("expected SearchConsoleError")


def test_exchange_requires_refresh_token() -> None:
    client = _FakeClient({"access_token": "at"})
    try:
        exchange_authorization_code("code", "verifier", settings=_settings(), client=client)
    except SearchConsoleError as exc:
        assert "refresh token" in str(exc).lower()
    else:
        raise AssertionError("expected SearchConsoleError")
    assert client.calls[0][1]["data"]["grant_type"] == "authorization_code"
    assert client.calls[0][1]["data"]["code_verifier"] == "verifier"
    assert "oauth-secret" in client.calls[0][1]["data"]["client_secret"]


def test_exchange_returns_tokens() -> None:
    client = _FakeClient({"access_token": "at", "refresh_token": "rt"})
    body = exchange_authorization_code("code", "verifier", settings=_settings(), client=client)
    assert body["refresh_token"] == "rt"
    assert body["access_token"] == "at"
