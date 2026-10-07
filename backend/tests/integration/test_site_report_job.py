"""Site report job recrawls via the crawl handler, then audits, then stores HTML."""

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.intelligence.website.extract import extract_page
from app.jobs.handlers.site_report import site_report as run_site_report
from app.models.finding import AnalysisRun, AnalysisRunStatus
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.report import MetricSnapshot, SiteReport, SiteReportEmailStatus, SiteReportStatus
from app.services.mailer import DeliveryResult
from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.models.user import User
from app.models.website import CrawlRun, CrawlRunStatus, Website, WebsitePage


_REPO = Path(__file__).resolve().parents[3]
_GOLDEN_E = _REPO / "testdata" / "golden-projects" / "e-static-html"
_BASE = "https://golden-e.example"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db) -> Project:
    user = db.scalar(select(User).limit(1))
    if user is None:
        user = User(email=f"site-report-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"site-report-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj
    db.rollback()
    for job in db.scalars(select(Job).where(Job.project_id == proj.id)):
        db.delete(job)
    db.commit()
    row = db.get(Project, proj.id)
    if row is not None:
        db.delete(row)
        db.commit()


@pytest.fixture
def crawled_site(db, project) -> Website:
    website = Website(project_id=project.id, url=f"{_BASE}/index.html", platform="url_only")
    db.add(website)
    db.commit()
    db.refresh(website)
    run = CrawlRun(
        website_id=website.id,
        project_id=project.id,
        status=CrawlRunStatus.SUCCEEDED,
        stats_json={
            "crawl": {
                "failed_urls": [],
                "page_count": 5,
                "sitemap": {"page_url_count": 5},
            }
        },
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    for name in ("index.html", "about.html", "products.html", "contact.html", "orphan.html"):
        html = (_GOLDEN_E / name).read_text(encoding="utf-8")
        page = extract_page(html, url=f"{_BASE}/{name}").page
        db.add(
            WebsitePage(
                website_id=website.id,
                crawl_run_id=run.id,
                url=page.url,
                status_code=page.status_code,
                title=page.title,
                canonical=page.canonical,
                model_json=page.model_dump(mode="json"),
            )
        )
    db.commit()
    return website


def _progress(_stage: str, _percent: int, _message: str) -> None:
    return None


def test_site_report_reuses_existing_crawl_and_stores_gsc_totals(
    db, project, crawled_site, monkeypatch
) -> None:
    page_url = f"{_BASE}/index.html"
    order: list[str] = []

    def _fake_crawl(job, _db, report_progress):
        order.append("crawl")
        report_progress("Crawling", 100, "using stored crawl")

    def _fake_audit(job, db, report_progress):
        order.append("audit")
        now = datetime.now(timezone.utc)
        run = AnalysisRun(
            project_id=job.project_id,
            job_id=job.id,
            status=AnalysisRunStatus.SUCCEEDED,
            scores_json={
                "technical_seo_health": {
                    "key": "technical_seo_health",
                    "label": "Technical SEO Health",
                    "value": 80,
                    "max_value": 100,
                    "signals": [],
                }
            },
            inputs_json={"search_console": "attached"},
            gaps_json=[],
            started_at=now,
            finished_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        gsc_rows = [
            SearchConsoleRow(
                project_id=job.project_id,
                analysis_run_id=run.id,
                dimension=SearchConsoleDimension.PAGE,
                page=page_url,
                query=None,
                impressions=180000,
                clicks=1440,
                ctr=0.008,
                position=12.3,
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            ),
            SearchConsoleRow(
                project_id=job.project_id,
                analysis_run_id=run.id,
                dimension=SearchConsoleDimension.QUERY,
                page=None,
                query="crm software",
                impressions=90000,
                clicks=80,
                ctr=0.0009,
                position=18.0,
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            ),
            SearchConsoleRow(
                project_id=job.project_id,
                analysis_run_id=run.id,
                dimension=SearchConsoleDimension.DEVICE,
                page=None,
                query="MOBILE",
                impressions=120000,
                clicks=900,
                ctr=0.0075,
                position=13.0,
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            ),
        ]
        for day in range(1, 61):
            month = 7 if day <= 31 else 8
            d = day if day <= 31 else day - 31
            gsc_rows.append(
                SearchConsoleRow(
                    project_id=job.project_id,
                    analysis_run_id=run.id,
                    dimension=SearchConsoleDimension.DATE,
                    page=None,
                    query=None,
                    impressions=2000 + day,
                    clicks=20 + (day % 5),
                    ctr=0.01,
                    position=12.0,
                    start_date=date(2026, month, d),
                    end_date=date(2026, month, d),
                    fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
                )
            )
        db.add_all(gsc_rows)
        db.commit()
        report_progress("Finishing", 100, "audit=succeeded")

    monkeypatch.setattr("app.jobs.handlers.site_report.website_crawl", _fake_crawl)
    monkeypatch.setattr("app.jobs.handlers.site_report.audit", _fake_audit)
    monkeypatch.setattr(
        "app.jobs.handlers.site_report.deliver_site_report",
        lambda **_kwargs: DeliveryResult(SiteReportEmailStatus.SENT, "sent to test@example.com"),
    )

    job = Job(project_id=project.id, type="site_report", status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)
    run_site_report(job, db, _progress)
    assert order == ["crawl", "audit"]

    report = db.scalar(
        select(SiteReport).where(SiteReport.project_id == project.id).order_by(SiteReport.id.desc())
    )
    assert report is not None
    assert report.status in {SiteReportStatus.SUCCEEDED, SiteReportStatus.PARTIAL}
    assert report.email_status is SiteReportEmailStatus.SENT
    assert report.email_detail == "sent to test@example.com"
    assert report.html is not None
    assert "180,000" in report.html
    assert "not Google Index Coverage" in report.html
    assert "crm software" in report.html
    assert "Last 7 days vs prior 7 days" in report.html
    assert "<svg" in report.html
    assert "MOBILE" in report.html
    document = report.document_json or {}
    windows = {item["key"]: item for item in document.get("period_comparisons") or []}
    assert windows["week"]["status"] == "observed"
    assert windows["month"]["status"] == "observed"
    totals = document.get("totals") or {}
    assert totals["impressions"]["value"] == 180000
    assert totals["clicks"]["value"] == 1440

    analysis = db.scalar(
        select(AnalysisRun).where(AnalysisRun.job_id == job.id).order_by(AnalysisRun.id.desc())
    )
    assert analysis is not None
    assert report.analysis_run_id == analysis.id

    snapshot = db.scalar(
        select(MetricSnapshot)
        .where(MetricSnapshot.project_id == project.id)
        .order_by(MetricSnapshot.id.desc())
    )
    assert snapshot is not None
    assert snapshot.site_report_id == report.id
