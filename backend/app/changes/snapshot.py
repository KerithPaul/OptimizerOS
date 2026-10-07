"""Workspace snapshot (step 7.2, `[SPEC AGENTS.md §33]`).

A snapshot is taken **before** any modification: a raw file copy of the
cloned workspace (excluding `.git`, which git history already preserves,
and generated/dependency directories — see `_SNAPSHOT_EXCLUDE` — which
`restore_snapshot` never needs and a real `node_modules` tree can break
on Windows) plus the current HEAD commit hash. `restore_snapshot` copies specific
files back from it — used when a change is rejected after already being
written to disk (step 7.3 writes files; nothing is committed until
Phase 9, so a rejected change must not leave the workspace dirty). This
is a plain restore point, not Phase 8's AST-aware semantic rollback.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.intelligence.repository.clone import rmtree_readonly_safe, run_git, workspace_path
from app.models.change import Snapshot, SnapshotReason
from app.models.job import Job
from app.models.repository import Repository

logger = logging.getLogger("architectos.changes.snapshot")

# Generated/dependency directories, never restored by `restore_snapshot`
# (it only ever copies back the Code Agent's own written files, which
# `scope_forbidden_directories` — app.core.config — already keeps out of
# node_modules) and safe to skip: reproducible via install/build, and on
# Windows a real pnpm node_modules tree reliably breaks a raw copytree —
# deeply nested .pnpm store paths exceed MAX_PATH, and hoisted-dependency
# symlinks raise WinError/EINVAL under shutil's default copy.
_SNAPSHOT_EXCLUDE = (
    ".git", "node_modules", ".next", ".turbo", "dist", "build",
    ".venv", "__pycache__",
)


class SnapshotError(Exception):
    """The workspace could not be snapshotted. Modification must not proceed."""


def _snapshot_root(project_id: int, repository_id: int, settings: Settings) -> Path:
    return settings.resolved_workspace_root / str(project_id) / f"{repository_id}__snapshots"


def _current_commit_hash(workspace: Path) -> str | None:
    try:
        return run_git(["rev-parse", "HEAD"], cwd=workspace).strip()
    except Exception as exc:  # noqa: BLE001 - a missing/broken .git must not block a snapshot
        logger.warning("could not read HEAD commit for snapshot (workspace=%s): %s", workspace, exc)
        return None


def create_snapshot(
    db: Session,
    *,
    repository: Repository,
    job: Job | None,
    reason: SnapshotReason = SnapshotReason.BEFORE_CODE_CHANGE,
    settings: Settings | None = None,
) -> Snapshot:
    """Copy the current workspace aside and record it. Must run before any write."""

    settings = settings or get_settings()
    workspace = workspace_path(repository.project_id, repository.id, settings)
    if not workspace.is_dir():
        raise SnapshotError(f"workspace does not exist: {workspace}")

    snapshot_id = uuid.uuid4().hex
    dest = _snapshot_root(repository.project_id, repository.id, settings) / snapshot_id
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copytree(
            workspace,
            dest,
            ignore=shutil.ignore_patterns(*_SNAPSHOT_EXCLUDE),
        )
    except OSError as exc:
        if dest.exists():
            rmtree_readonly_safe(dest, ignore_errors=True)
        raise SnapshotError(f"failed to copy workspace to snapshot: {exc}") from exc

    row = Snapshot(
        project_id=repository.project_id,
        repository_id=repository.id,
        job_id=job.id if job is not None else None,
        reason=reason,
        commit_hash=_current_commit_hash(workspace),
        snapshot_path=str(dest),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "snapshot created (project_id=%s, repository_id=%s, snapshot_id=%s, path=%s)",
        repository.project_id,
        repository.id,
        row.id,
        dest,
    )
    return row


def restore_snapshot(snapshot: Snapshot, file_paths: list[str], *, settings: Settings | None = None) -> None:
    """Copy specific files back from `snapshot`, restoring their pre-change content.

    A file that did not exist in the snapshot (the change created it) is
    removed instead — the snapshot's absence of the file *is* its
    pre-change state.
    """

    settings = settings or get_settings()
    if snapshot.repository_id is None:
        raise SnapshotError("snapshot has no repository_id; cannot resolve the workspace")
    workspace = workspace_path(snapshot.project_id, snapshot.repository_id, settings)
    snapshot_root = Path(snapshot.snapshot_path)
    if not snapshot_root.is_dir():
        raise SnapshotError(f"snapshot path does not exist: {snapshot_root}")

    for rel in file_paths:
        src = snapshot_root / rel
        dst = workspace / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()
    logger.info(
        "snapshot restored (snapshot_id=%s, files=%s)", snapshot.id, len(file_paths)
    )


def _file_snapshot_root(project_id: int, repository_id: int, settings: Settings) -> Path:
    return settings.resolved_workspace_root / str(project_id) / f"{repository_id}__file_originals"


def create_file_snapshot(
    db: Session,
    *,
    repository: Repository,
    finding_id: str,
    file_path: str,
    settings: Settings | None = None,
) -> tuple[Snapshot, bool]:
    """Capture one file's exact pre-modification content (guided-fix STEP 3).

    Returns `(snapshot, created)`. Idempotent: if a guided-fix snapshot
    for this finding already records the file, that snapshot is returned
    with `created=False` and the stored original is **never** overwritten
    — the first capture is the original of record. The content lives in
    `files_json` AND as a raw file on disk under the snapshots tree, so
    the original survives even if the row were edited.
    """

    settings = settings or get_settings()
    workspace = workspace_path(repository.project_id, repository.id, settings)
    rel = file_path.replace("\\", "/").strip("/")
    full = workspace / rel
    if not full.is_file():
        raise SnapshotError(f"file does not exist in the workspace: {rel}")
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SnapshotError(f"could not read the file: {exc}") from exc

    existing = list(
        db.scalars(
            select(Snapshot).where(
                Snapshot.project_id == repository.project_id,
                Snapshot.repository_id == repository.id,
                Snapshot.reason == SnapshotReason.BEFORE_GUIDED_FIX,
            )
        )
    )
    for row in existing:
        for entry in row.files_json or []:
            if isinstance(entry, dict) and entry.get("file_path") == rel \
                    and entry.get("finding_id") == finding_id:
                return row, False

    snapshot_id = uuid.uuid4().hex
    dest_dir = _file_snapshot_root(repository.project_id, repository.id, settings) / snapshot_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        (dest_dir / "original.txt").write_text(content, encoding="utf-8")
    except OSError as exc:
        raise SnapshotError(f"could not persist the original file: {exc}") from exc

    row = Snapshot(
        project_id=repository.project_id,
        repository_id=repository.id,
        job_id=None,
        reason=SnapshotReason.BEFORE_GUIDED_FIX,
        commit_hash=_current_commit_hash(workspace),
        snapshot_path=str(dest_dir),
        files_json=[
            {
                "file_path": rel,
                "finding_id": finding_id,
                "content": content,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "file snapshot created (project_id=%s, repository_id=%s, snapshot_id=%s, file=%s)",
        repository.project_id,
        repository.id,
        row.id,
        rel,
    )
    return row, True
