"""Typed request/response models for Search Console and experiments (Phase 11)."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.search import ExperimentStatus, ExperimentType, SearchConsoleDimension


class SearchConsoleConnectionIn(BaseModel):
    property_url: str


class SearchConsoleConnectionOut(BaseModel):
    connected: bool
    platform: str | None = None
    auth_type: str | None = None
    has_stored_credentials: bool
    oauth_configured: bool
    oauth_redirect_uri: str
    property_url: str | None = None
    properties: list[str] = []
    status: str
    display: str


class SearchConsoleRowOut(BaseModel):
    id: int
    dimension: SearchConsoleDimension
    query: str | None
    page: str | None
    impressions: int
    clicks: int
    ctr: float
    position: float
    start_date: date
    end_date: date
    fetched_at: datetime

    model_config = {"from_attributes": True}


class SearchConsoleProjectOut(BaseModel):
    connection: SearchConsoleConnectionOut
    rows: list[SearchConsoleRowOut]


class ExperimentCreateIn(BaseModel):
    hypothesis: str
    experiment_type: ExperimentType
    change_set_id: int | None = None
    finding_ids: list[str] | None = None


class ExperimentOut(BaseModel):
    id: int
    project_id: int
    change_set_id: int | None
    experiment_type: ExperimentType
    hypothesis: str
    change_json: dict[str, Any] | None
    baseline_metrics: dict[str, Any] | None
    treatment_metrics: dict[str, Any] | None
    result: dict[str, Any] | None
    confidence: float | None
    status: ExperimentStatus
    created_at: datetime
    baseline_recorded_at: datetime | None
    treatment_recorded_at: datetime | None
    finished_at: datetime | None
    causation: str = Field(default="not_claimed")
    causation_note: str = Field(
        default="Observed metric movement is correlation, not evidence that the change caused it."
    )

    model_config = {"from_attributes": True}
