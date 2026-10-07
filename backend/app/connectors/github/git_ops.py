"""Git CLI branch, logical commit, and push (step 9.3, [P15]).

One commit per Change Set — never one commit per tiny file. The PAT is
injected only into the push URL for that process; it is never written
into `.git/config`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.connectors.github.auth import authenticated_https_url, redact_secret
from app.intelligence.repository.clone import CloneError, run_git

_SLUG_SAFE = re.compile(r"[^a-z0-9]+")
_COMMIT_IDENTITY = (
    "-c",
    "user.name=ArchitectOS",
    "-c",
    "user.email=architectos@local",
)


class GitOpsError(Exception):
    """Local git or push failed. Message is already redacted."""


@dataclass(frozen=True)
class GitCommitResult:
    sha: str
    branch: str
    message: str
    files: tuple[str, ...]


def branch_name(change_set_id: int, slug: str) -> str:
    cleaned = slugify(slug)
    return f"architectos/{change_set_id}-{cleaned}"


def slugify(text: str, *, max_length: int = 40) -> str:
    slug = _SLUG_SAFE.sub("-", text.lower()).strip("-")
    if not slug:
        slug = "change"
    return slug[:max_length].strip("-") or "change"


def commit_and_push(
    workspace: Path,
    *,
    files: list[str],
    message: str,
    branch: str,
    remote_url: str,
    token: str,
) -> GitCommitResult:
    """Create `branch`, one logical commit of `files`, and push it.

    `files` is the Change Set's affected resources. Unrelated dirty files
    stay unstaged.
    """

    if not files:
        raise GitOpsError("refusing to commit an empty file list")
    if not (workspace / ".git").exists():
        raise GitOpsError("workspace is not a git repository")

    unique_files = _unique_existing(workspace, files)
    if not unique_files:
        raise GitOpsError("none of the Change Set files exist on disk")

    try:
        _git(workspace, ["checkout", "-B", branch], token)
        _git(workspace, ["add", "--", *unique_files], token)
        staged = _git(workspace, ["diff", "--cached", "--name-only"], token).strip()
        if not staged:
            raise GitOpsError("no staged changes for this Change Set")
        _git(workspace, [*_COMMIT_IDENTITY, "commit", "-m", message], token)
        sha = _git(workspace, ["rev-parse", "HEAD"], token).strip()
        push_target = authenticated_https_url(remote_url, token)
        _git(
            workspace,
            ["push", "--set-upstream", push_target, f"HEAD:refs/heads/{branch}"],
            token,
        )
    except GitOpsError:
        raise
    except CloneError as exc:
        raise GitOpsError(redact_secret(str(exc), token)) from exc

    return GitCommitResult(
        sha=sha, branch=branch, message=message, files=tuple(unique_files)
    )


def _unique_existing(workspace: Path, files: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in files:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or normalized in seen:
            continue
        if not (workspace / normalized).exists():
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _git(workspace: Path, args: list[str], token: str) -> str:
    try:
        return run_git(args, cwd=workspace)
    except CloneError as exc:
        raise GitOpsError(redact_secret(str(exc), token)) from exc
