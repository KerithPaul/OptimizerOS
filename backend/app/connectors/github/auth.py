"""GitHub credentials on `platform_connections`.

OAuth 2.0 is the primary flow. An encrypted PAT remains a fallback
(`auth_type=pat`). Tokens are decrypted only inside connector and git
code — never in an LLM prompt path, never returned by an API, never
interpolated into a log line.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.connectors.capabilities import dump_report, github_token_capabilities
from app.core.crypto import decrypt, encrypt
from app.models.website import PlatformConnection

GITHUB_PLATFORM = "github"
GITHUB_AUTH_TYPE = "pat"
GITHUB_AUTH_TYPE_OAUTH = "oauth2"

_TOKEN_IN_URL = re.compile(r"(x-access-token:)[^@]+@", re.IGNORECASE)
_REFRESH_SKEW_SECONDS = 60


class GitHubAuthError(Exception):
    """No usable GitHub credential, or the ciphertext cannot be decrypted."""


def github_connection(db: Session, project_id: int) -> PlatformConnection | None:
    return db.scalar(
        select(PlatformConnection).where(
            PlatformConnection.project_id == project_id,
            PlatformConnection.platform == GITHUB_PLATFORM,
        )
    )


def _capability_payload(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dump_report(github_token_capabilities())
    if existing:
        for key in ("login", "selected_repo"):
            if existing.get(key):
                payload[key] = existing[key]
    return payload


def store_pat(db: Session, project_id: int, token: str) -> PlatformConnection:
    cleaned = token.strip()
    if not cleaned:
        raise GitHubAuthError("GitHub PAT must not be empty")
    connection = github_connection(db, project_id)
    report = _capability_payload(
        connection.capabilities_json if connection is not None else None
    )
    if connection is None:
        connection = PlatformConnection(
            website_id=None,
            project_id=project_id,
            platform=GITHUB_PLATFORM,
            auth_type=GITHUB_AUTH_TYPE,
            capabilities_json=report,
            credentials_encrypted=encrypt(cleaned),
        )
        db.add(connection)
    else:
        connection.auth_type = GITHUB_AUTH_TYPE
        connection.capabilities_json = report
        connection.credentials_encrypted = encrypt(cleaned)
        flag_modified(connection, "capabilities_json")
    db.commit()
    db.refresh(connection)
    return connection


def store_oauth_tokens(
    db: Session,
    project_id: int,
    tokens: dict[str, Any],
    *,
    login: str | None = None,
) -> PlatformConnection:
    access = str(tokens.get("access_token") or "").strip()
    if not access:
        raise GitHubAuthError("GitHub OAuth token response had no access_token")
    blob: dict[str, Any] = {
        "type": GITHUB_AUTH_TYPE_OAUTH,
        "access_token": access,
        "token_type": str(tokens.get("token_type") or "bearer"),
        "scope": str(tokens.get("scope") or ""),
    }
    refresh = str(tokens.get("refresh_token") or "").strip()
    if refresh:
        blob["refresh_token"] = refresh
    expires_in = tokens.get("expires_in")
    if expires_in is not None:
        try:
            blob["expires_at"] = int(
                datetime.now(timezone.utc).timestamp() + int(expires_in)
            )
        except (TypeError, ValueError):
            pass

    connection = github_connection(db, project_id)
    existing = (
        dict(connection.capabilities_json)
        if connection is not None and isinstance(connection.capabilities_json, dict)
        else {}
    )
    report = _capability_payload(existing)
    if login:
        report["login"] = login.strip()
    ciphertext = encrypt(json.dumps(blob, separators=(",", ":")))
    if connection is None:
        connection = PlatformConnection(
            website_id=None,
            project_id=project_id,
            platform=GITHUB_PLATFORM,
            auth_type=GITHUB_AUTH_TYPE_OAUTH,
            capabilities_json=report,
            credentials_encrypted=ciphertext,
        )
        db.add(connection)
    else:
        connection.auth_type = GITHUB_AUTH_TYPE_OAUTH
        connection.capabilities_json = report
        connection.credentials_encrypted = ciphertext
        flag_modified(connection, "capabilities_json")
    db.commit()
    db.refresh(connection)
    return connection


def store_selected_repo(
    db: Session, project_id: int, full_name: str
) -> PlatformConnection:
    cleaned = full_name.strip().strip("/")
    if not cleaned or cleaned.count("/") != 1:
        raise GitHubAuthError("repository must be owner/repo")
    connection = github_connection(db, project_id)
    if connection is None or not connection.credentials_encrypted:
        raise GitHubAuthError(
            "Connect GitHub with OAuth (or a PAT) before selecting a repository"
        )
    caps = dict(connection.capabilities_json or {})
    caps["selected_repo"] = cleaned
    connection.capabilities_json = caps
    flag_modified(connection, "capabilities_json")
    db.commit()
    db.refresh(connection)
    return connection


def clear_pat(db: Session, project_id: int) -> None:
    connection = github_connection(db, project_id)
    if connection is None:
        return
    db.delete(connection)
    db.commit()


def decrypt_credentials(connection: PlatformConnection) -> str:
    if connection.platform != GITHUB_PLATFORM:
        raise GitHubAuthError("platform_connection is not GitHub")
    if not connection.credentials_encrypted:
        raise GitHubAuthError("GitHub credentials are not stored")
    return decrypt(connection.credentials_encrypted)


def decrypt_pat(connection: PlatformConnection) -> str:
    """Return the plaintext access token (PAT or OAuth). Caller must not log it."""

    return access_token_from_raw(decrypt_credentials(connection))


def _parse_blob(raw: str) -> dict[str, Any] | None:
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("access_token"):
        return data
    return None


def access_token_from_raw(raw: str) -> str:
    blob = _parse_blob(raw)
    if blob is not None:
        return str(blob["access_token"])
    return raw.strip()


def _blob_expired(blob: dict[str, Any]) -> bool:
    expires_at = blob.get("expires_at")
    if expires_at is None:
        return False
    try:
        return float(expires_at) <= (
            datetime.now(timezone.utc).timestamp() + _REFRESH_SKEW_SECONDS
        )
    except (TypeError, ValueError):
        return False


def require_github_token(
    db: Session, project_id: int
) -> tuple[PlatformConnection, str]:
    connection = github_connection(db, project_id)
    if connection is None:
        raise GitHubAuthError("GitHub is not connected for this project")
    raw = decrypt_credentials(connection)
    blob = _parse_blob(raw)
    if blob is None:
        token = raw.strip()
        if not token:
            raise GitHubAuthError("GitHub PAT is not stored")
        return connection, token

    if _blob_expired(blob) and blob.get("refresh_token"):
        from app.connectors.github.oauth import GitHubOAuthError, refresh_access_token

        try:
            with httpx.Client(timeout=20.0) as client:
                refreshed = refresh_access_token(
                    str(blob["refresh_token"]), client=client
                )
        except GitHubOAuthError as exc:
            raise GitHubAuthError(str(exc)) from exc
        if not refreshed.get("refresh_token"):
            refreshed["refresh_token"] = blob["refresh_token"]
        login = None
        caps = connection.capabilities_json
        if isinstance(caps, dict):
            login = caps.get("login")
        connection = store_oauth_tokens(
            db, project_id, refreshed, login=str(login) if login else None
        )
        return connection, str(refreshed["access_token"])

    access = str(blob.get("access_token") or "").strip()
    if not access:
        raise GitHubAuthError("GitHub OAuth access token is missing")
    return connection, access


def require_pat(db: Session, project_id: int) -> tuple[PlatformConnection, str]:
    return require_github_token(db, project_id)


def connection_login(connection: PlatformConnection | None) -> str | None:
    if connection is None or not isinstance(connection.capabilities_json, dict):
        return None
    login = connection.capabilities_json.get("login")
    return str(login) if login else None


def selected_repo(connection: PlatformConnection | None) -> str | None:
    if connection is None or not isinstance(connection.capabilities_json, dict):
        return None
    name = connection.capabilities_json.get("selected_repo")
    return str(name) if name else None


def github_capability_report(connection: PlatformConnection | None):
    from app.connectors.capabilities import CapabilityReport, report_from_json

    if connection is None or not isinstance(connection.capabilities_json, dict):
        return None
    fields = set(CapabilityReport.model_fields)
    return report_from_json(
        {k: v for k, v in connection.capabilities_json.items() if k in fields}
    )


def redact_secret(text: str, secret: str | None) -> str:
    if not text:
        return text
    redacted = _TOKEN_IN_URL.sub(r"\1***@", text)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


def authenticated_https_url(url: str, token: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return url
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"x-access-token:{token}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def parse_github_repo(url: str) -> tuple[str, str]:
    """Return (owner, repo) from a github.com remote URL or owner/repo."""

    raw = url.strip()
    if raw.startswith("git@"):
        _, _, rest = raw.partition(":")
        path = rest
    elif _is_owner_repo(raw):
        path = raw
    else:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        if host not in {"github.com", "www.github.com"}:
            raise GitHubAuthError(f"not a github.com URL: {url}")
        path = parts.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    segments = [part for part in path.split("/") if part]
    if len(segments) < 2:
        raise GitHubAuthError(f"cannot parse owner/repo from {url}")
    return segments[0], segments[1]


def _is_owner_repo(raw: str) -> bool:
    if "://" in raw or raw.startswith("git@") or "\\" in raw:
        return False
    parts = [part for part in raw.strip("/").split("/") if part]
    return len(parts) == 2 and not parts[0].endswith(":")
