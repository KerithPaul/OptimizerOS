"""Site health reports (recrawl + audit + GSC snapshot + assembled document)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.models.report import SiteReport
from app.models.user import User
from app.schemas.job import JobOut
from app.schemas.site_report import SiteReportOut, SiteReportSummaryOut

router = APIRouter(
    prefix="/projects/{project_id}/site-reports",
    tags=["site-reports"],
    dependencies=[Depends(require_project_access)],
)


def _summary(row: SiteReport) -> SiteReportSummaryOut:
    document = row.document_json if isinstance(row.document_json, dict) else {}
    return SiteReportSummaryOut(
        id=row.id,
        project_id=row.project_id,
        job_id=row.job_id,
        analysis_run_id=row.analysis_run_id,
        crawl_run_id=row.crawl_run_id,
        status=row.status,
        email_status=row.email_status,
        email_detail=row.email_detail,
        created_at=row.created_at,
        finished_at=row.finished_at,
        website_url=document.get("website_url"),
        search_console=document.get("search_console"),
        finding_count=document.get("finding_count"),
        open_finding_count=document.get("open_finding_count"),
        period=document.get("period"),
        totals=document.get("totals"),
        causation=document.get("causation"),
    )


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def start_site_report(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> JobOut:
    return enqueue(db, project_id, "site_report")


@router.get("", response_model=list[SiteReportSummaryOut])
def list_site_reports(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SiteReportSummaryOut]:
    rows = list(
        db.scalars(
            select(SiteReport)
            .where(SiteReport.project_id == project_id)
            .order_by(SiteReport.id.desc())
        )
    )
    return [_summary(row) for row in rows]


@router.get("/{report_id}", response_model=SiteReportOut)
def get_site_report(
    project_id: int,
    report_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SiteReportOut:
    row = db.get(SiteReport, report_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "site report not found")
    summary = _summary(row)
    return SiteReportOut(
        **summary.model_dump(),
        gaps_json=row.gaps_json,
        metrics_json=row.metrics_json,
        deltas_json=row.deltas_json,
        document_json=row.document_json,
        html=row.html,
    )
