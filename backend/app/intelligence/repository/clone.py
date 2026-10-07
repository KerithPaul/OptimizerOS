"""Git CLI clone into the project workspace (step 2.B.2).

GitHub MCP is not used here. Public HTTPS by default; an encrypted token
on the repository row, if present, is decrypted only inside this module
and never logged. A failed clone raises `CloneError` after recording
`clone_failed` so analysis must not proceed.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt
from app.models.repository import CloneStatus, Repository

logger = logging.getLogger("architectos.intelligence.repository.clone")

class CloneError(Exception):
    """Git clone failed. The repository row is already `clone_failed`."""


def _clear_readonly_and_retry(func, path: str, exc_info) -> None:
    """`onerror` hook for `shutil.rmtree`.

    Git marks pack/idx files read-only; Windows refuses to unlink a
    read-only file even with delete permission, so a plain `rmtree` on a
    previously cloned `.git` directory raises WinError 5. Clear the
    attribute and retry once.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _win_long_path(path: Path) -> Path:
    """Prefix `path` with `\\\\?\\` on Windows to opt out of MAX_PATH (260 chars).

    A pnpm-installed `node_modules/.pnpm/<encoded-dependency-key>/node_modules/...`
    tree routinely produces paths past that limit; without the prefix, Win32
    file APIs (and CPython's wrappers around them) report the file as
    missing (`WinError 3`) instead of naming the real problem, so
    `shutil.rmtree` fails deep inside a stale workspace it needs to clear.
    """
    if os.name != "nt":
        return path
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\"):
        return path
    if resolved.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved.lstrip("\\"))
    return Path("\\\\?\\" + resolved)


def rmtree_readonly_safe(path: Path, *, ignore_errors: bool = False) -> None:
    shutil.rmtree(
        _win_long_path(path), ignore_errors=ignore_errors, onerror=_clear_readonly_and_retry
    )


def workspace_path(
    project_id: int,
    repository_id: int,
    settings: Settings | None = None,
) -> Path:
    settings = settings or get_settings()
    return settings.resolved_workspace_root / str(project_id) / str(repository_id)


def clone_repository(
    db: Session,
    repository: Repository,
    settings: Settings | None = None,
) -> Path:
    """Clone `repository.url` into WORKSPACE_ROOT/{project_id}/{repository_id}.

    Records the exact HEAD commit on success. On any failure sets
    `clone_status=clone_failed`, commits, and raises `CloneError`.
    """
    settings = settings or get_settings()
    dest = workspace_path(repository.project_id, repository.id, settings)

    repository.clone_status = CloneStatus.CLONING
    repository.cloned_commit_hash = None
    db.commit()

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            rmtree_readonly_safe(dest)

        clone_url, redacted = _clone_url(db, repository, settings)
        run_git(
            [
                "clone",
                "--branch",
                repository.default_branch,
                "--single-branch",
                clone_url,
                str(dest),
            ],
            cwd=None,
        )
        commit = run_git(["rev-parse", "HEAD"], cwd=dest).strip()
    except CloneError as exc:
        repository.clone_status = CloneStatus.CLONE_FAILED
        repository.cloned_commit_hash = None
        db.commit()
        if dest.exists():
            rmtree_readonly_safe(dest, ignore_errors=True)
        logger.error(
            "git clone failed (repository_id=%s, url=%s): %s",
            repository.id,
            repository.url,
            exc,
        )
        raise
    except Exception as exc:
        repository.clone_status = CloneStatus.CLONE_FAILED
        repository.cloned_commit_hash = None
        db.commit()
        if dest.exists():
            rmtree_readonly_safe(dest, ignore_errors=True)
        logger.error(
            "git clone failed (repository_id=%s, url=%s): %s",
            repository.id,
            repository.url,
            exc,
        )
        raise CloneError(str(exc)) from exc

    repository.clone_status = CloneStatus.CLONED
    repository.cloned_commit_hash = commit
    db.commit()
    logger.info(
        "git clone succeeded (repository_id=%s, url=%s, commit=%s, dest=%s)",
        repository.id,
        redacted,
        commit,
        dest,
    )
    return dest


def _clone_url(
    db: Session, repository: Repository, settings: Settings
) -> tuple[str, str]:
    """Return (url_for_git, url_safe_to_log). Token never appears in the log URL.

    Prefer the repository clone token. Otherwise use the project's GitHub
    OAuth/PAT connection for github.com remotes.
    """
    token = resolve_clone_token(db, repository, settings)
    if not token:
        return repository.url, repository.url
    return _inject_token(repository.url, token), repository.url


def resolve_clone_token(
    db: Session, repository: Repository, settings: Settings
) -> str | None:
    if repository.clone_token_encrypted:
        return decrypt(repository.clone_token_encrypted, settings)
    from app.connectors.github.auth import (
        GitHubAuthError,
        parse_github_repo,
        require_github_token,
    )

    try:
        parse_github_repo(repository.url)
    except GitHubAuthError:
        return None
    try:
        _connection, token = require_github_token(db, repository.project_id)
    except GitHubAuthError:
        return None
    return token


def _inject_token(url: str, token: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return url
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"x-access-token:{token}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def run_git(args: list[str], cwd: Path | None) -> str:
    git = shutil.which("git")
    if git is None:
        raise CloneError("git executable not found on PATH")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    timeout = get_settings().git_timeout_seconds
    try:
        completed = subprocess.run(
            [git, *args],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # Not `str(exc)`: it embeds the full argv, including any token in the URL.
        raise CloneError(
            f"git {args[0]} timed out after {timeout}s. The repository may be "
            "very large; raise GIT_TIMEOUT_SECONDS if this is expected."
        ) from None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git failed").strip()
        raise CloneError(_redact_credentials(detail))
    return completed.stdout


def _redact_credentials(text: str) -> str:
    """Strip `user:secret@` from any URL in git output before it is surfaced."""
    return re.sub(r"(https?://)[^/\s@]+@", r"\1***@", text)
