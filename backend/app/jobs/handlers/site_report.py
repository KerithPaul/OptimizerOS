"""Site health report job: recrawl → audit → measure → assemble → email.

One heavy job holds the slot for the full cycle so crawl and audit are
not queued behind each other. Email failure is recorded on the report and
does not fail the job.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.jobs.handlers.audit import audit
from app.jobs.handlers.website_crawl import website_crawl
from app.jobs.registry import ProgressReporter, register
from app.models.finding import AnalysisRun, AnalysisRunStatus
from app.models.job import Job
from app.models.project import Project
from app.models.report import MetricSnapshot, SiteReport, SiteReportEmailStatus, SiteReportStatus
from app.models.search import SearchConsoleRow
from app.models.website import CrawlRun, Website
from app.services.mailer import deliver_site_report
from app.services.measurement import capture_metrics, compare_metrics
from app.services.search_console import snapshot_from_rows
from app.services.site_report import assemble_document, render_html
from app.services.site_report_schedule import load_config, recipients_for


def _scale(report_progress: ProgressReporter, lo: int, hi: int) -> ProgressReporter:
    def inner(stage: str, percent: int, message: str) -> None:
        mapped = lo + int((hi - lo) * max(0, min(100, percent)) / 100)
        report_progress(stage, mapped, message)

    return inner


def _latest_crawl(db: Session, project_id: int) -> CrawlRun | None:
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    if website is None:
        return None
    return db.scalar(
        select(CrawlRun).where(CrawlRun.website_id == website.id).order_by(CrawlRun.id.desc())
    )


def _latest_analysis_for_job(db: Session, job_id: int) -> AnalysisRun | None:
    return db.scalar(
        select(AnalysisRun).where(AnalysisRun.job_id == job_id).order_by(AnalysisRun.id.desc())
    )


def _previous_snapshot(db: Session, project_id: int, current_id: int) -> MetricSnapshot | None:
    return db.scalar(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.project_id == project_id,
            MetricSnapshot.id != current_id,
        )
        .order_by(MetricSnapshot.id.desc())
    )


def site_report(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    project = db.get(Project, job.project_id)
    if project is None:
        raise RuntimeError("project not found")

    report = SiteReport(
        project_id=job.project_id,
        job_id=job.id,
        status=SiteReportStatus.RUNNING,
        email_status=SiteReportEmailStatus.UNAVAILABLE,
        email_detail="SMTP is not configured; the report is stored in ArchitectOS",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    gaps: list[dict[str, str]] = []
    try:
        website = db.scalar(select(Website).where(Website.project_id == job.project_id))
        report_progress("Crawling", 5, "Refreshing site facts")
        if website is None:
            gaps.append({"capability": "website", "detail": "no website attached"})
        else:
            try:
                website_crawl(job, db, _scale(report_progress, 5, 40))
            except Exception as exc:
                gaps.append({"capability": "website_crawl", "detail": str(exc)})

        report_progress("Auditing", 42, "Evaluating rules and fetching Search Console")
        try:
            audit(job, db, _scale(report_progress, 42, 78))
        except Exception as exc:
            gaps.append({"capability": "audit", "detail": str(exc)})

        crawl = _latest_crawl(db, job.project_id)
        analysis = _latest_analysis_for_job(db, job.id)
        if analysis is not None and isinstance(analysis.gaps_json, list):
            for item in analysis.gaps_json:
                if isinstance(item, dict) and item.get("capability"):
                    gaps.append(
                        {
                            "capability": str(item.get("capability")),
                            "detail": str(item.get("detail") or ""),
                        }
                    )

        report_progress("Measuring", 80, "Capturing current metrics")
        gsc_stmt = select(SearchConsoleRow).where(SearchConsoleRow.project_id == job.project_id)
        if analysis is not None:
            gsc_stmt = gsc_stmt.where(SearchConsoleRow.analysis_run_id == analysis.id)
        gsc_rows = list(db.scalars(gsc_stmt))
        gsc_snapshot = snapshot_from_rows(gsc_rows, property_url=None) if gsc_rows else None
        metrics = capture_metrics(
            db, job.project_id, snapshot=gsc_snapshot, fetch_gsc=False
        )
        snapshot = MetricSnapshot(
            project_id=job.project_id,
            site_report_id=report.id,
            captured_at=datetime.now(timezone.utc),
            metrics_json=metrics,
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        previous = _previous_snapshot(db, job.project_id, snapshot.id)
        deltas = (
            compare_metrics(previous.metrics_json, metrics)
            if previous is not None
            else None
        )

        report_progress("Assembling", 90, "Writing the site health report")
        document = assemble_document(
            db,
            project=project,
            analysis_run=analysis,
            crawl=crawl,
            website=website,
            metrics=metrics,
            deltas=deltas,
            gaps=_dedupe_gaps(gaps),
        )
        html = render_html(document)
        report.analysis_run_id = analysis.id if analysis is not None else None
        report.crawl_run_id = crawl.id if crawl is not None else None
        report.gaps_json = document["gaps"]
        report.metrics_json = metrics
        report.deltas_json = deltas
        report.document_json = document
        report.html = html
        report.status = _report_status(analysis, gaps)
        report.finished_at = datetime.now(timezone.utc)
        db.commit()
        _deliver_email(report, db, report_progress)
        report_progress(
            "Finishing",
            100,
            f"site_report={report.status.value} email={report.email_status.value} analysis_run={report.analysis_run_id}",
        )
    except Exception as exc:
        report.status = SiteReportStatus.FAILED
        report.gaps_json = _dedupe_gaps(
            gaps + [{"capability": "site_report", "detail": str(exc)}]
        )
        report.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise


def _deliver_email(report: SiteReport, db: Session, report_progress: ProgressReporter) -> None:
    if report.status is SiteReportStatus.FAILED or not report.html:
        report.email_status = SiteReportEmailStatus.UNAVAILABLE
        report.email_detail = "report did not complete; email skipped"
        db.commit()
        return
    report_progress("Emailing", 96, "Sending the site health report")
    settings = get_settings()
    config = load_config(settings)
    item = next((row for row in config.schedules if row.project_id == report.project_id), None)
    try:
        result = deliver_site_report(
            html=report.html,
            document=report.document_json if isinstance(report.document_json, dict) else {},
            recipients=recipients_for(item, config, settings),
            settings=settings,
        )
    except Exception as exc:
        report.email_status = SiteReportEmailStatus.FAILED
        report.email_detail = str(exc)[:512]
        db.commit()
        return
    report.email_status = result.status
    report.email_detail = result.detail
    db.commit()


def _report_status(analysis: AnalysisRun | None, gaps: list[dict[str, str]]) -> SiteReportStatus:
    if analysis is None:
        return SiteReportStatus.FAILED
    if analysis.status is AnalysisRunStatus.FAILED or analysis.status is AnalysisRunStatus.PARTIAL or gaps:
        return SiteReportStatus.PARTIAL
    return SiteReportStatus.SUCCEEDED


def _dedupe_gaps(gaps: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for gap in gaps:
        key = (gap.get("capability", ""), gap.get("detail", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"capability": key[0], "detail": key[1]})
    return out


register("site_report", site_report)
