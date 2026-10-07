"""GitHub PAT storage and redaction (step 9.1 verify)."""

from cryptography.fernet import Fernet

from types import SimpleNamespace

from app.connectors.github.auth import (
    access_token_from_raw,
    authenticated_https_url,
    parse_github_repo,
    redact_secret,
)
from app.intelligence.repository.clone import resolve_clone_token
from app.core.config import Settings
from app.core.crypto import encrypt


def test_parse_https_and_ssh_github_urls() -> None:
    assert parse_github_repo("https://github.com/acme/shop.git") == ("acme", "shop")
    assert parse_github_repo("git@github.com:acme/shop.git") == ("acme", "shop")
    assert parse_github_repo("acme/shop") == ("acme", "shop")


def test_access_token_from_raw_oauth_blob_and_pat() -> None:
    assert access_token_from_raw("ghp_plain") == "ghp_plain"
    blob = '{"type":"oauth2","access_token":"gho_from_blob","refresh_token":"r1"}'
    assert access_token_from_raw(blob) == "gho_from_blob"


def test_resolve_clone_token_uses_github_connection(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.connectors.github.auth.require_github_token",
        lambda db, project_id: (object(), "gho_oauth_token"),
    )
    repo = SimpleNamespace(
        clone_token_encrypted=None,
        url="https://github.com/acme/shop.git",
        project_id=9,
    )
    assert resolve_clone_token(None, repo, None) == "gho_oauth_token"


def test_resolve_clone_token_skips_non_github_url(monkeypatch) -> None:
    repo = SimpleNamespace(
        clone_token_encrypted=None,
        url=str("C:/tmp/local-repo"),
        project_id=9,
    )
    assert resolve_clone_token(None, repo, None) is None


def test_redact_secret_strips_token_from_url_and_text() -> None:
    token = "ghp_supersecret"
    text = f"fatal: https://x-access-token:{token}@github.com/acme/shop.git"
    redacted = redact_secret(text, token)
    assert token not in redacted
    assert "***" in redacted


def test_authenticated_url_does_not_change_file_remotes() -> None:
    assert authenticated_https_url("/tmp/origin.git", "secret") == "/tmp/origin.git"


def test_ciphertext_is_not_the_pat() -> None:
    settings = Settings(
        APP_SECRET_KEY="unit-test-secret",
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    token = "ghp_never-log-this"
    ciphertext = encrypt(token, settings)
    assert token.encode() not in ciphertext
