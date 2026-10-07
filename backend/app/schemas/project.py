"""Typed request/response models for the projects API (AGENTS.md §59)."""

from datetime import datetime

from pydantic import BaseModel

from app.models.project import ProjectMode


class ProjectCreate(BaseModel):
    name: str
    mode: ProjectMode = ProjectMode.AUDIT_ONLY


class ProjectModeUpdate(BaseModel):
    mode: ProjectMode


class ProjectOut(BaseModel):
    id: int
    name: str
    mode: ProjectMode
    created_by: int
    created_at: datetime

    model_config = {"from_attributes": True}
