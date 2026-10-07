"""Clone job: success records commit_hash; failure is clone_failed and
does not profile (step 2.B.2 verify).
"""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.repository.clone import workspace_path
from app.jobs.queue import enqueue, get_redis
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.repository import CloneStatus, Repository
from app.models.user import User
from app.worker import run_job

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"


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
        user = User(email=f"clone-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)

    proj = Project(name=f"clone-test-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj

    db.delete(proj)
    db.commit()


def _cleanup_job(db, job: Job) -> None:
    row = db.get(Job, job.id)
    if row is not None:
        db.delete(row)
        db.commit()


def _init_git_copy(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest)
    subprocess.run(["git", "init", "-b", "main"], cwd=dest, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "architectos-test@example.com"],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "ArchitectOS Test"],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=dest, check=True, capture_output=True)


def _run_queued_job(db, project_id: int) -> Job:
    settings = get_settings()
    job = enqueue(db, project_id, "repository_clone", settings)
    get_redis(settings).brpop([settings.job_queue_key], timeout=5)
    worker_db = SessionLocal()
    try:
        run_job(worker_db, job.id, settings)
    finally:
        worker_db.close()
    db.rollback()
    return db.get(Job, job.id)


def test_failed_clone_marks_job_failed_and_repository_clone_failed(db, project) -> None:
    repository = Repository(
        project_id=project.id,
        url=str(Path("/no/such/architectos-repo.git")),
        default_branch="main",
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    job = _run_queued_job(db, project.id)
    try:
        db.refresh(repository)
        assert job.status == JobStatus.FAILED
        assert job.status != JobStatus.SUCCEEDED
        assert repository.clone_status == CloneStatus.CLONE_FAILED
        assert repository.cloned_commit_hash is None
        assert repository.architecture_profile is None
        dest = workspace_path(project.id, repository.id)
        assert not dest.exists()
    finally:
        _cleanup_job(db, job)


def test_successful_clone_records_commit_and_profiles_golden_a(db, project, tmp_path) -> None:
    source = tmp_path / "src"
    _init_git_copy(_GOLDEN_A, source)
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    repository = Repository(
        project_id=project.id,
        url=str(source),
        default_branch="main",
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    job = _run_queued_job(db, project.id)
    dest = workspace_path(project.id, repository.id)
    try:
        db.refresh(repository)
        assert job.status == JobStatus.SUCCEEDED
        assert repository.clone_status == CloneStatus.CLONED
        assert repository.cloned_commit_hash == expected_commit
        assert repository.architecture_profile is not None
        assert repository.architecture_profile["framework"] == "Next.js"
        assert repository.architecture_profile["language"] == "TypeScript"
        assert repository.architecture_profile["database"] == "MySQL"
        assert repository.architecture_profile["used_llm"] is False
        assert job.progress_json is not None
        assert "included=" in job.progress_json["message"]
        assert dest.is_dir()
        assert (dest / "package.json").is_file()
    finally:
        _cleanup_job(db, job)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
