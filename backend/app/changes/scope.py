"""Scope enforcer (step 7.4, `[SPEC AGENTS.md §33]`).

Compares the Change Plan's own declared files against the Code Agent's
actual files, plus a hard denylist that applies regardless of what any
plan says. The canonical example:

    Expected: app/products/**
    Actual:   auth/**, database/**

must be rejected. Stopping is a first-class outcome — `check_scope`
returns a `ScopeViolation` value, it never raises for an in-bounds vs.
out-of-bounds diff (only for e.g. a missing envelope).
"""

from __future__ import annotations

import difflib
import posixpath
from dataclasses import dataclass

from app.core.config import Settings
from app.planners.change import ChangePlan


@dataclass(frozen=True)
class ScopeEnvelope:
    """The `[SPEC]` scope: allowed directories, forbidden directories, limits."""

    allowed_directories: tuple[str, ...]
    forbidden_directories: tuple[str, ...]
    max_files_changed: int
    max_lines_changed: int
    max_diff_bytes: int


@dataclass(frozen=True)
class FileDiff:
    file_path: str
    lines_added: int
    lines_removed: int
    diff_bytes: int
    unified_diff: str


@dataclass(frozen=True)
class ScopeViolation:
    reason: str
    detail: str


def _normalize(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/")).lstrip("/")


def is_path_safe(path: str) -> bool:
    """Reject absolute paths and any path that escapes the workspace root.

    This is independent of the scope envelope: the envelope only compares
    *planned* vs *actual* paths, and both could in principle name the same
    traversal string. A path must clear this check before it is ever read
    from or written to disk, and before it is allowed into the envelope's
    allowed-directory derivation.
    """

    raw = path.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return False
    normalized = posixpath.normpath(raw)
    return normalized != ".." and not normalized.startswith("../")


def _directory_of(path: str) -> str:
    directory = posixpath.dirname(_normalize(path))
    return directory


def envelope_for_plan(settings: Settings, change_plan: ChangePlan) -> ScopeEnvelope:
    """Derive the allowed-directory set from the Change Plan's own target files.

    Each target file's top-level directory becomes an allowed prefix (e.g.
    `app/products/page.tsx` -> `app/products/**`), matching the plan's own
    canonical example. A target file with no directory (repo root) allows
    only exact-name matches at the root.
    """

    allowed: set[str] = set()
    for raw in change_plan.target_files:
        if not is_path_safe(raw):
            continue
        directory = _directory_of(raw)
        allowed.add(directory)
    return ScopeEnvelope(
        allowed_directories=tuple(sorted(allowed)),
        forbidden_directories=settings.scope_forbidden_directory_list,
        max_files_changed=settings.scope_max_files_changed,
        max_lines_changed=settings.scope_max_lines_changed,
        max_diff_bytes=settings.scope_max_diff_bytes,
    )


def diff_stats(file_path: str, before: str, after: str) -> FileDiff:
    """Deterministic line/byte diff — never asks the LLM to self-report size."""

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(before_lines, after_lines, fromfile=file_path, tofile=file_path)
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    unified = "".join(diff_lines)
    return FileDiff(
        file_path=file_path,
        lines_added=added,
        lines_removed=removed,
        diff_bytes=len(unified.encode("utf-8")),
        unified_diff=unified,
    )


def _within_allowed(path: str, allowed_directories: tuple[str, ...]) -> bool:
    directory = _directory_of(path)
    for allowed in allowed_directories:
        if directory == allowed or (allowed and directory.startswith(f"{allowed}/")):
            return True
    return False


def _within_forbidden(path: str, forbidden_directories: tuple[str, ...]) -> str | None:
    normalized = _normalize(path)
    for forbidden in forbidden_directories:
        forbidden = forbidden.strip("/")
        if not forbidden:
            continue
        if normalized == forbidden or normalized.startswith(f"{forbidden}/"):
            return forbidden
    return None


def check_scope(
    envelope: ScopeEnvelope,
    actual_files: list[str],
    diffs: list[FileDiff],
) -> ScopeViolation | None:
    """Return the first violation found, or `None` when the change is in bounds."""

    if not actual_files:
        return ScopeViolation("no_files", "the patch changed no files")

    for path in actual_files:
        if not is_path_safe(path):
            return ScopeViolation("path_traversal", f"'{path}' escapes the workspace root")

    for path in actual_files:
        forbidden = _within_forbidden(path, envelope.forbidden_directories)
        if forbidden is not None:
            return ScopeViolation(
                "forbidden_directory",
                f"'{path}' falls under the forbidden directory '{forbidden}/**'",
            )

    for path in actual_files:
        if not _within_allowed(path, envelope.allowed_directories):
            allowed = ", ".join(f"{d}/**" for d in envelope.allowed_directories) or "(none)"
            return ScopeViolation(
                "outside_planned_scope",
                f"'{path}' is outside the Change Plan's scope (expected {allowed})",
            )

    if len(actual_files) > envelope.max_files_changed:
        return ScopeViolation(
            "too_many_files",
            f"{len(actual_files)} files changed, exceeds max_files_changed="
            f"{envelope.max_files_changed}",
        )

    total_lines = sum(item.lines_added + item.lines_removed for item in diffs)
    if total_lines > envelope.max_lines_changed:
        return ScopeViolation(
            "too_many_lines",
            f"{total_lines} lines changed, exceeds max_lines_changed="
            f"{envelope.max_lines_changed}",
        )

    total_bytes = sum(item.diff_bytes for item in diffs)
    if total_bytes > envelope.max_diff_bytes:
        return ScopeViolation(
            "diff_too_large",
            f"{total_bytes} diff bytes, exceeds max_diff_bytes={envelope.max_diff_bytes}",
        )

    return None
