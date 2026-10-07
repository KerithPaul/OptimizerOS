"""Typed request/response models for attaching a git repository (step 2.B)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.repository import CloneStatus


class RepositoryCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    default_branch: str = Field("main", min_length=1, max_length=255)
    clone_token: str | None = None


class RepositoryTokenUpdate(BaseModel):
    """Set or clear the clone token on an existing repository.

    An empty/omitted `clone_token` clears it, reverting to an anonymous clone.
    """

    clone_token: str | None = None


class RepositoryOut(BaseModel):
    id: int
    project_id: int
    url: str
    default_branch: str
    cloned_commit_hash: str | None
    clone_status: CloneStatus
    last_indexed_commit: str | None
    architecture_profile: dict | None
    created_at: datetime
    indexing_state: str | None = None
    framework: str | None = None
    has_clone_token: bool = False

    model_config = {"from_attributes": True}


class FileOut(BaseModel):
    path: str
    language: str | None = None


class SymbolOut(BaseModel):
    name: str
    kind: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    role: str | None = None


class RouteOut(BaseModel):
    path: str
    file_path: str | None = None


class NeighborOut(BaseModel):
    name: str
    kind: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    relation: str | None = None
