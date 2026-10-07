"""GitHub OAuth 2.0 authorization URL and token exchange."""

from cryptography.fernet import Fernet

from app.connectors.github.oauth import (
    GitHubOAuthError,
    build_authorization_url,
    exchange_authorization_code,
    oauth_configured,
    refresh_access_token,
)


def _settings(**overrides):
    from app.core.config import Settings

    values = dict(
        APP_SECRET_KEY="unit-test-secret",
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        GITHUB_OAUTH_CLIENT_ID="ov_github_client",
        GITHUB_OAUTH_CLIENT_SECRET="github-oauth-secret",
        GITHUB_OAUTH_REDIRECT_URI="http://localhost:8000/api/v1/github/oauth/callback",
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
        oauth_configured(_settings(GITHUB_OAUTH_CLIENT_ID="", GITHUB_OAUTH_CLIENT_SECRET="x"))
        is False
    )


def test_authorization_url_is_github_oauth_with_pkce() -> None:
    url, state, verifier, redirect_uri = build_authorization_url(42, settings=_settings())
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "code_challenge_method=S256" in url
    assert "scope=repo" in url
    assert "ov_github_client" in url
    assert redirect_uri.endswith("/github/oauth/callback")
    assert "redirect_uri=" in url
    assert verifier
    assert state
    assert "github-oauth-secret" not in url


def test_authorization_url_uses_request_derived_redirect_when_env_blank() -> None:
    url, _state, _verifier, redirect_uri = build_authorization_url(
        1,
        settings=_settings(GITHUB_OAUTH_REDIRECT_URI=""),
        redirect_uri="http://localhost:6001/api/v1/github/oauth/callback",
    )
    assert redirect_uri == "http://localhost:6001/api/v1/github/oauth/callback"
    assert "localhost%3A6001" in url or "localhost:6001" in url


def test_authorization_url_without_client_fails() -> None:
    try:
        build_authorization_url(
            1, settings=_settings(GITHUB_OAUTH_CLIENT_ID="", GITHUB_OAUTH_CLIENT_SECRET="")
        )
    except GitHubOAuthError as exc:
        assert "OAuth is not configured" in str(exc)
    else:
        raise AssertionError("expected GitHubOAuthError")


def test_exchange_accepts_access_token_without_refresh() -> None:
    client = _FakeClient({"access_token": "gho_at", "token_type": "bearer", "scope": "repo"})
    body = exchange_authorization_code("code", "verifier", settings=_settings(), client=client)
    assert body["access_token"] == "gho_at"
    assert client.calls[0][1]["data"]["code_verifier"] == "verifier"
    assert "github-oauth-secret" in client.calls[0][1]["data"]["client_secret"]
    assert client.calls[0][1]["headers"]["Accept"] == "application/json"


def test_exchange_rejects_github_error_payload() -> None:
    client = _FakeClient(
        {"error": "bad_verification_code", "error_description": "The code passed is incorrect."}
    )
    try:
        exchange_authorization_code("code", "verifier", settings=_settings(), client=client)
    except GitHubOAuthError as exc:
        assert "incorrect" in str(exc).lower()
    else:
        raise AssertionError("expected GitHubOAuthError")


def test_refresh_posts_refresh_token_grant() -> None:
    client = _FakeClient({"access_token": "gho_new", "refresh_token": "r2"})
    body = refresh_access_token("r1", settings=_settings(), client=client)
    assert body["access_token"] == "gho_new"
    assert client.calls[0][1]["data"]["grant_type"] == "refresh_token"
    assert client.calls[0][1]["data"]["refresh_token"] == "r1"
