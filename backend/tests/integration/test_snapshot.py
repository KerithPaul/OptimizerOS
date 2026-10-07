"""Workspace snapshot (step 7.2 verify, `[SPEC AGENTS.md §33]`).

No patch path can run without a snapshot record existing first — this
verifies the snapshot actually captures file content (not just a DB row)
and that `restore_snapshot` reverses an in-place edit exactly, including
removing a file the "change" created that did not exist beforehand.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.changes.snapshot import SnapshotError, create_snapshot, restore_snapshot
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.repository.clone import workspace_path
from app.models.change import Snapshot
from app.models.job import Job
from app.models.project import Project
from app.models.repository import CloneStatus, Repository
from app.models.user import User


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db) -> Project:
    user = db.scalar(select(User).limit(1))
    if user is None:
        user = User(email=f"snapshot-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"snapshot-test-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj
    db.rollback()
    for row in db.scalars(select(Snapshot).where(Snapshot.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(Repository).where(Repository.project_id == proj.id)):
        db.delete(row)
    db.commit()
    row = db.get(Project, proj.id)
    if row is not None:
        db.delete(row)
        db.commit()


@pytest.fixture
def cloned_repository(db, project) -> Repository:
    repository = Repository(
        project_id=project.id,
        url="https://example.invalid/repo.git",
        default_branch="main",
        clone_status=CloneStatus.CLONED,
        cloned_commit_hash="deadbeef",
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    settings = get_settings()
    dest = workspace_path(project.id, repository.id, settings)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "app").mkdir(parents=True, exist_ok=True)
    (dest / "app" / "page.tsx").write_text("export default function Page() {}", encoding="utf-8")
    (dest / "README.md").write_text("hello", encoding="utf-8")

    yield repository

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    snapshot_root = dest.parent / f"{repository.id}__snapshots"
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root, ignore_errors=True)


def test_snapshot_requires_an_existing_workspace(db, project) -> None:
    repository = Repository(
        project_id=project.id,
        url="https://example.invalid/repo.git",
        default_branch="main",
        clone_status=CloneStatus.CLONED,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    with pytest.raises(SnapshotError):
        create_snapshot(db, repository=repository, job=None)


def test_snapshot_copies_files_and_restore_reverses_an_edit(db, cloned_repository) -> None:
    settings = get_settings()
    workspace = workspace_path(cloned_repository.project_id, cloned_repository.id, settings)

    snapshot = create_snapshot(db, repository=cloned_repository, job=None)
    assert Path(snapshot.snapshot_path).is_dir()
    assert (Path(snapshot.snapshot_path) / "app" / "page.tsx").read_text(encoding="utf-8") == (
        "export default function Page() {}"
    )

    # Simulate the Code Agent editing an existing file and creating a new one.
    (workspace / "app" / "page.tsx").write_text("export const x = 1;", encoding="utf-8")
    (workspace / "app" / "new-file.tsx").write_text("export const y = 2;", encoding="utf-8")

    restore_snapshot(snapshot, ["app/page.tsx", "app/new-file.tsx"], settings=settings)

    assert (workspace / "app" / "page.tsx").read_text(encoding="utf-8") == (
        "export default function Page() {}"
    )
    assert not (workspace / "app" / "new-file.tsx").exists()


def test_snapshot_excludes_node_modules(db, cloned_repository) -> None:
    """`restore_snapshot` only ever copies back Code Agent source files
    (never node_modules, which `scope_forbidden_directories` already keeps
    the agent from touching), and on Windows a real pnpm node_modules tree
    reliably breaks a raw copytree — deeply nested .pnpm store paths exceed
    MAX_PATH, hoisted-dependency symlinks raise WinError/EINVAL."""
    settings = get_settings()
    workspace = workspace_path(cloned_repository.project_id, cloned_repository.id, settings)
    nested = workspace / "node_modules" / "some-pkg"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "index.js").write_text("module.exports = {};", encoding="utf-8")

    snapshot = create_snapshot(db, repository=cloned_repository, job=None)

    assert not (Path(snapshot.snapshot_path) / "node_modules").exists()
    assert (Path(snapshot.snapshot_path) / "app" / "page.tsx").is_file()


def test_snapshot_row_persists_commit_hash_field(db, cloned_repository) -> None:
    snapshot = create_snapshot(db, repository=cloned_repository, job=None)
    row = db.get(Snapshot, snapshot.id)
    assert row is not None
    assert row.project_id == cloned_repository.project_id
    assert row.repository_id == cloned_repository.id
    # The fixture workspace has no `.git` of its own (unlike a real clone),
    # so `_current_commit_hash` either resolves an ancestor repo's HEAD or
    # returns None -- either way it must not raise (`SnapshotError`).
    assert row.commit_hash is None or isinstance(row.commit_hash, str)
