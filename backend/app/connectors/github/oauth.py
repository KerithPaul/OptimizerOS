"""GitHub OAuth 2.0 (authorization code + PKCE).

Mirrors Search Console: client id/secret live in env, tokens are encrypted
on `platform_connections`, and the token never appears in logs or LLM
context. Classic OAuth Apps issue a long-lived access token; refresh
tokens are stored when GitHub returns them (token expiration enabled).
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings, get_settings

GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_OAUTH_SCOPES = "repo read:user"
OAUTH_COOKIE_NAME = "architectos_github_oauth"
OAUTH_MAX_AGE_SECONDS = 600
_OAUTH_SALT = "architectos-github-oauth"

DEFAULT_CALLBACK = "http://localhost:8000/api/v1/github/oauth/callback"


class GitHubOAuthError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def oauth_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        (settings.github_oauth_client_id or "").strip()
        and (settings.github_oauth_client_secret or "").strip()
    )


def oauth_redirect_uri(
    settings: Settings | None = None, *, request_base_url: str | None = None
) -> str:
    settings = settings or get_settings()
    configured = (settings.github_oauth_redirect_uri or "").strip()
    if configured:
        return configured.rstrip("/")
    if request_base_url:
        origin = str(request_base_url).rstrip("/")
        return f"{origin}/api/v1/github/oauth/callback"
    return DEFAULT_CALLBACK


def _oauth_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key, salt=_OAUTH_SALT)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_authorization_url(
    project_id: int,
    *,
    settings: Settings | None = None,
    redirect_uri: str | None = None,
) -> tuple[str, str, str, str]:
    """Return (github_auth_url, signed_state, code_verifier, redirect_uri)."""

    settings = settings or get_settings()
    if not oauth_configured(settings):
        raise GitHubOAuthError(
            "GitHub OAuth is not configured (set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET)"
        )
    resolved_redirect = (redirect_uri or oauth_redirect_uri(settings)).strip()
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    nonce = _b64url(os.urandom(16))
    state = _oauth_serializer(settings).dumps({"project_id": project_id, "nonce": nonce})
    params = {
        "client_id": settings.github_oauth_client_id.strip(),
        "redirect_uri": resolved_redirect,
        "scope": GITHUB_OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "allow_signup": "false",
    }
    return (
        f"{GITHUB_OAUTH_AUTHORIZE}?{urlencode(params)}",
        state,
        verifier,
        resolved_redirect,
    )


def pack_oauth_cookie(
    project_id: int,
    verifier: str,
    state: str,
    settings: Settings,
    *,
    redirect_uri: str,
) -> str:
    return _oauth_serializer(settings).dumps(
        {
            "project_id": project_id,
            "verifier": verifier,
            "state": state,
            "redirect_uri": redirect_uri,
        }
    )


def unpack_oauth_cookie(token: str, settings: Settings) -> dict[str, Any]:
    try:
        data = _oauth_serializer(settings).loads(token, max_age=OAUTH_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise GitHubOAuthError(
            "OAuth session expired; start Connect with GitHub again"
        ) from exc
    if not isinstance(data, dict) or "verifier" not in data or "project_id" not in data:
        raise GitHubOAuthError("OAuth session is invalid")
    return data


def load_oauth_state(state: str, settings: Settings) -> dict[str, Any]:
    try:
        data = _oauth_serializer(settings).loads(state, max_age=OAUTH_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise GitHubOAuthError("OAuth state expired or invalid") from exc
    if not isinstance(data, dict) or "project_id" not in data:
        raise GitHubOAuthError("OAuth state is invalid")
    return data


def _redact(text: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    secret = (settings.github_oauth_client_secret or "").strip()
    if secret:
        return text.replace(secret, "***")
    return text


def _parse_token_body(body: Any, settings: Settings, *, require_refresh: bool) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise GitHubOAuthError("GitHub OAuth token response was not JSON")
    if body.get("error"):
        description = body.get("error_description") or body.get("error")
        raise GitHubOAuthError(f"GitHub OAuth: {_redact(str(description), settings)}")
    access = body.get("access_token")
    if not access:
        raise GitHubOAuthError("GitHub OAuth token response had no access_token")
    if require_refresh and not body.get("refresh_token"):
        raise GitHubOAuthError("GitHub OAuth token response had no refresh_token")
    return body


def exchange_authorization_code(
    code: str,
    verifier: str,
    *,
    settings: Settings | None = None,
    redirect_uri: str | None = None,
    client: httpx.Client,
) -> dict[str, Any]:
    settings = settings or get_settings()
    data = {
        "client_id": settings.github_oauth_client_id.strip(),
        "client_secret": settings.github_oauth_client_secret.strip(),
        "code": code.strip(),
        "redirect_uri": (redirect_uri or oauth_redirect_uri(settings)).strip(),
        "code_verifier": verifier,
    }
    try:
        response = client.post(
            GITHUB_OAUTH_TOKEN,
            data=data,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise GitHubOAuthError(_redact(str(exc), settings)) from exc
    if response.status_code >= 400:
        raise GitHubOAuthError(
            f"GitHub OAuth token {response.status_code}: {_redact(response.text, settings)}",
            status_code=response.status_code,
        )
    return _parse_token_body(response.json(), settings, require_refresh=False)


def refresh_access_token(
    refresh_token: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client,
) -> dict[str, Any]:
    settings = settings or get_settings()
    data = {
        "client_id": settings.github_oauth_client_id.strip(),
        "client_secret": settings.github_oauth_client_secret.strip(),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        response = client.post(
            GITHUB_OAUTH_TOKEN,
            data=data,
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise GitHubOAuthError(_redact(str(exc), settings)) from exc
    if response.status_code >= 400:
        raise GitHubOAuthError(
            f"GitHub OAuth refresh {response.status_code}: {_redact(response.text, settings)}",
            status_code=response.status_code,
        )
    return _parse_token_body(response.json(), settings, require_refresh=False)
