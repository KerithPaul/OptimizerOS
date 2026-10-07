"""Typed request/response models for the jobs API (AGENTS.md §59)."""

from datetime import datetime

from pydantic import BaseModel

from app.models.job import JobStatus


class JobCreate(BaseModel):
    project_id: int
    type: str


class JobOut(BaseModel):
    id: int
    project_id: int
    type: str
    status: JobStatus
    progress_json: dict | None
    error: str | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}
