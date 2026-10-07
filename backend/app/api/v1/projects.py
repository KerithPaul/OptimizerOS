"""List, create, get, update-mode, and delete endpoints for projects.

A project created with no explicit mode stores AUDIT_ONLY (CONFIRMED C3).
Raising a mode the project cannot perform is rejected from 3.A.3 using
the project-level capability report (website connector ∪ git source).

Visibility: members only see projects they created; admins see everything.
A member's `project_limit` (users.project_limit, NULL = unlimited) caps how
many projects they may create.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.access import assert_project_access
from app.api.v1.auth import get_current_user
from app.api.v1.websites import load_capability_report
from app.connectors.capabilities import ModeNotAllowed, assert_mode_allowed
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User, UserRole
from app.schemas.project import ProjectCreate, ProjectModeUpdate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


def _require_visible_project(project_id: int, user: User, db: Session) -> Project:
    """Load a project the user is allowed to act on (shared access guard)."""

    return assert_project_access(db, user, project_id)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Project]:
    stmt = select(Project).order_by(Project.id)
    if current_user.role != UserRole.ADMIN:
        stmt = stmt.where(Project.created_by == current_user.id)
    return list(db.scalars(stmt))


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    if current_user.role != UserRole.ADMIN and current_user.project_limit is not None:
        owned = db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.created_by == current_user.id)
        )
        if owned is not None and int(owned) >= current_user.project_limit:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"your plan allows {current_user.project_limit} project"
                f"{'' if current_user.project_limit == 1 else 's'}; ask an admin to raise the limit",
            )
    project = Project(name=payload.name, mode=payload.mode, created_by=current_user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    return _require_visible_project(project_id, current_user, db)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Delete a project and all of its related rows (FK ON DELETE CASCADE)."""
    project = _require_visible_project(project_id, current_user, db)
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{project_id}/mode", response_model=ProjectOut)
def update_project_mode(
    project_id: int,
    payload: ProjectModeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    project = _require_visible_project(project_id, current_user, db)
    report = load_capability_report(db, project_id)
    try:
        assert_mode_allowed(payload.mode, report)
    except ModeNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    project.mode = payload.mode
    db.commit()
    db.refresh(project)
    return project
