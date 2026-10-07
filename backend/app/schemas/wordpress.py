"""Typed request/response models for the WordPress API (step 10.7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.connectors.capabilities import CapabilityReport


class WordPressConnectionIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=255)
    application_password: str = Field(min_length=1, max_length=255)


class WordPressConnectionOut(BaseModel):
    connected: bool
    platform: str = "wordpress"
    auth_type: str = "application_password"
    has_credentials: bool
    website_id: int | None = None
    url: str | None = None
    seo_plugin: str | None = None
    capabilities: CapabilityReport | None = None


class WordPressPageOut(BaseModel):
    url: str
    title: str | None
    meta_description: str | None
    canonical: str | None
    rest_base: str | None = None
    wordpress_id: int | None = None


class WordPressRevisionOut(BaseModel):
    id: int
    parent: int | None = None
    date: str | None = None
    title: str | None = None
    author: int | None = None


class WordPressProjectOut(BaseModel):
    connection: WordPressConnectionOut
    pages: list[WordPressPageOut]
    revisions: list[WordPressRevisionOut] = Field(default_factory=list)


class WordPressPublishOut(BaseModel):
    change_set_id: int
    snapshot_id: int | None = None
    error: str | None = None


class WordPressSnapshotOut(BaseModel):
    id: int
    project_id: int
    reason: str
    snapshot_path: str
    files_json: list | None
    created_at: datetime
    extra: dict[str, Any] | None = None

    model_config = {"from_attributes": True}
