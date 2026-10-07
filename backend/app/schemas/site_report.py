"""Request/response models for site health reports."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.report import SiteReportEmailStatus, SiteReportStatus
from app.schemas.job import JobOut


class SiteReportSummaryOut(BaseModel):
    id: int
    project_id: int
    job_id: int | None
    analysis_run_id: int | None
    crawl_run_id: int | None
    status: SiteReportStatus
    email_status: SiteReportEmailStatus
    email_detail: str | None
    created_at: datetime
    finished_at: datetime | None
    website_url: str | None = None
    search_console: str | None = None
    finding_count: int | None = None
    open_finding_count: int | None = None
    period: dict[str, Any] | None = None
    totals: dict[str, Any] | None = None
    causation: str | None = None

    model_config = {"from_attributes": True}


class SiteReportOut(SiteReportSummaryOut):
    gaps_json: list | None
    metrics_json: dict | None
    deltas_json: dict | None
    document_json: dict | None
    html: str | None


class SiteReportJobOut(BaseModel):
    job: JobOut
    report_hint: str = "poll the job, then GET /site-reports for the stored document"
