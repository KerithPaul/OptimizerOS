"""Attach a git repository and expose code intelligence (steps 2.B / 2.D.4).

v1 allows one repository per project. The clone token, if supplied, is
encrypted at rest and is never returned.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.core.crypto import encrypt
from app.db.session import get_db
from app.intelligence.repository.graph import GraphError
from app.intelligence.repository.queries import (
    list_files,
    list_routes,
    list_symbols,
    what_calls,
    what_depends_on,
    what_imports,
    what_routes_to,
)
from app.jobs.queue import enqueue
from app.models.job import Job
from app.models.project import Project
from app.models.repository import CloneStatus, Repository
from app.models.user import User
from app.schemas.job import JobOut
from app.schemas.repository import (
    FileOut,
    NeighborOut,
    RepositoryCreate,
    RepositoryOut,
    RepositoryTokenUpdate,
    RouteOut,
    SymbolOut,
)

router = APIRouter(tags=["repositories"])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.post(
    "/projects/{project_id}/repository",
    response_model=RepositoryOut,
    status_code=status.HTTP_201_CREATED,
)
def attach_repository(
    project_id: int,
    payload: RepositoryCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> RepositoryOut:
    _get_project(db, project_id)
    existing = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "project already has a repository",
        )
    token = payload.clone_token.strip() if payload.clone_token else ""
    repository = Repository(
        project_id=project_id,
        url=payload.url.strip(),
        default_branch=payload.default_branch.strip() or "main",
        clone_token_encrypted=encrypt(token) if token else None,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return _repository_out(repository)


@router.get("/projects/{project_id}/repository", response_model=RepositoryOut | None)
def get_repository(
    project_id: int,
    optional: bool = Query(False, description="Return null instead of 404 when not attached yet"),
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> RepositoryOut | None:
    repository = _require_repository(db, project_id, optional=optional)
    if repository is None:
        return None
    return _repository_out(repository)


@router.patch("/projects/{project_id}/repository/token", response_model=RepositoryOut)
def update_repository_token(
    project_id: int,
    payload: RepositoryTokenUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> RepositoryOut:
    """Set or clear the clone token, e.g. to index a private repo (401 on clone)."""
    repository = _require_repository(db, project_id)
    token = payload.clone_token.strip() if payload.clone_token else ""
    repository.clone_token_encrypted = encrypt(token) if token else None
    db.commit()
    db.refresh(repository)
    return _repository_out(repository)


@router.post(
    "/projects/{project_id}/repository/index",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
)
def index_repository(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> Job:
    _require_repository(db, project_id)
    return enqueue(db, project_id, "repository_clone")


@router.get("/projects/{project_id}/repository/files", response_model=list[FileOut])
def repository_files(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> list[dict]:
    repository = _require_repository(db, project_id)
    return _graph_call(list_files, project_id, repository.id)


@router.get("/projects/{project_id}/repository/symbols", response_model=list[SymbolOut])
def repository_symbols(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> list[dict]:
    repository = _require_repository(db, project_id)
    return _graph_call(list_symbols, project_id, repository.id)


@router.get("/projects/{project_id}/repository/routes", response_model=list[RouteOut])
def repository_routes(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> list[dict]:
    repository = _require_repository(db, project_id)
    return _graph_call(list_routes, project_id, repository.id)


@router.get(
    "/projects/{project_id}/repository/neighbors",
    response_model=list[NeighborOut],
)
def repository_neighbors(
    project_id: int,
    name: str = Query(min_length=1),
    relation: str = Query("calls"),
    db: Session = Depends(get_db),
    _: User = Depends(require_project_access),
) -> list[dict]:
    repository = _require_repository(db, project_id)
    queries = {
        "calls": what_calls,
        "routes_to": what_routes_to,
        "imports": what_imports,
        "depends_on": what_depends_on,
    }
    fn = queries.get(relation)
    if fn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown relation: {relation}")
    try:
        return fn(project_id, name, repository_id=repository.id)
    except GraphError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def _require_repository(
    db: Session, project_id: int, *, optional: bool = False
) -> Repository | None:
    _get_project(db, project_id)
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if repository is None:
        if optional:
            return None
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    return repository


def _repository_out(repository: Repository) -> RepositoryOut:
    profile = repository.architecture_profile or {}
    framework = profile.get("framework") if isinstance(profile, dict) else None
    if repository.clone_status == CloneStatus.CLONE_FAILED:
        indexing_state = "clone_failed"
    elif repository.clone_status == CloneStatus.CLONING:
        indexing_state = "cloning"
    elif repository.last_indexed_commit and repository.last_indexed_commit == repository.cloned_commit_hash:
        indexing_state = "indexed"
    elif repository.clone_status == CloneStatus.CLONED:
        indexing_state = "cloned"
    else:
        indexing_state = "pending"
    return RepositoryOut(
        id=repository.id,
        project_id=repository.project_id,
        url=repository.url,
        default_branch=repository.default_branch,
        cloned_commit_hash=repository.cloned_commit_hash,
        clone_status=repository.clone_status,
        last_indexed_commit=repository.last_indexed_commit,
        architecture_profile=repository.architecture_profile,
        created_at=repository.created_at,
        indexing_state=indexing_state,
        framework=framework,
        has_clone_token=repository.clone_token_encrypted is not None,
    )


def _graph_call(fn, project_id: int, repository_id: int) -> list[dict]:
    try:
        return fn(project_id, repository_id)
    except GraphError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
