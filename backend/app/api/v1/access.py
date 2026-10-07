"""Shared project-access guard.

Members may only touch projects they created; admins any project. Applied
as a router-level dependency on every project-scoped router so the check
cannot be forgotten on a new endpoint. Cross-member access returns 404 (not
403) so project ids stay unguessable.
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User, UserRole


def assert_project_access(db: Session, user: User, project_id: int) -> Project:
    """Plain-function variant for handlers whose project id is not a path
    parameter (e.g. taken from a request body or a job row)."""

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    if user.role != UserRole.ADMIN and project.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def require_project_access(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Project:
    """FastAPI dependency: resolves `project_id` from the path and enforces
    visibility. Safe to mount at router level for `/projects/{project_id}/…`
    routers."""

    return assert_project_access(db, current_user, project_id)
