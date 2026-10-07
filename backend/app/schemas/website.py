"""Typed request/response models for websites and capability reports (3.A / 3.D)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.connectors.capabilities import CapabilityReport
from app.models.project import ProjectMode
from app.models.website import CrawlRunStatus


class WebsiteCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    platform: str = Field("url_only", min_length=1, max_length=50)


class CrawlRunOut(BaseModel):
    id: int
    website_id: int
    project_id: int
    status: CrawlRunStatus
    cap_reason: str | None
    error: str | None
    page_count: int | None = None
    stats_json: dict | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class WebsiteOut(BaseModel):
    id: int
    project_id: int
    url: str
    platform: str
    created_at: datetime
    capabilities: CapabilityReport
    auth_type: str
    latest_crawl: CrawlRunOut | None = None

    model_config = {"from_attributes": True}


class ProjectCapabilitiesOut(BaseModel):
    report: CapabilityReport | None
    allowed_modes: list[ProjectMode]


class PageSummaryOut(BaseModel):
    id: int
    website_id: int
    crawl_run_id: int | None
    url: str
    status_code: int | None
    title: str | None
    canonical: str | None
    fetch_ms: int | None = None
    word_count: int | None = None

    model_config = {"from_attributes": True}


class PageDetailOut(PageSummaryOut):
    model_json: dict | None
    lighthouse: dict | None = None
