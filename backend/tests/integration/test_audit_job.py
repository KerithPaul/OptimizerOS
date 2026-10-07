"""Audit job (step 5.B.3 verify)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.website.extract import extract_page
from app.jobs.queue import enqueue, get_redis
from app.models.finding import AnalysisRun, AnalysisRunStatus, Finding
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.user import User
from app.models.website import CrawlRun, CrawlRunStatus, Website, WebsitePage
from app.retrieval.hybrid import RetrievalResult
from app.services.vectors import VectorError
from app.worker import run_job

_REPO = Path(__file__).resolve().parents[3]
_GOLDEN_E = _REPO / "testdata" / "golden-projects" / "e-static-html"
_BASE = "https://golden-e.example"


def _golden_pages():
    pages = []
    for name in ("index.html", "about.html", "products.html", "contact.html", "orphan.html"):
        html = (_GOLDEN_E / name).read_text(encoding="utf-8")
        pages.append(extract_page(html, url=f"{_BASE}/{name}").page)
    return pages


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
        user = User(email=f"audit-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"audit-test-{uuid.uuid4()}", created_by=user.id)
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
                "failed_urls": [f"{_BASE}/missing.html"],
                "robots": {
                    "state": "fetched",
                    "http_status": 200,
                    "ai_bot_disallows": [],
                    "sitemap_urls": [],
                },
                "sitemap": {"records": [], "page_url_count": 0},
            }
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    for page in _golden_pages():
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


@pytest.fixture
def stub_retrieve(monkeypatch):
    def fake_retrieve(query: str, **_kwargs) -> RetrievalResult:
        return RetrievalResult(
            query=query,
            rewritten_query=query,
            rerank="fallback",
            rerank_reason="test",
            candidates_considered=0,
            items=[],
            llm_messages=[],
            gaps=[],
        )

    monkeypatch.setattr("app.jobs.handlers.audit.retrieve", fake_retrieve)
    return fake_retrieve


def _run_audit(db, project_id: int) -> Job:
    settings = get_settings()
    job = enqueue(db, project_id, "audit", settings)
    popped = get_redis(settings).brpop([settings.job_queue_key], timeout=5)
    assert popped is not None
    worker_db = SessionLocal()
    try:
        run_job(worker_db, job.id, settings)
    finally:
        worker_db.close()
    db.rollback()
    return db.get(Job, job.id)


def test_audit_of_golden_e_persists_grounded_findings(db, project, crawled_site, stub_retrieve) -> None:
    job = _run_audit(db, project.id)
    assert job.status is JobStatus.SUCCEEDED
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id)
        .order_by(AnalysisRun.id.desc())
    )
    assert run is not None
    assert run.inputs_json is not None
    assert run.inputs_json["search_console"] == "unavailable"
    assert run.inputs_json["search_console_display"] == "Search Console: unavailable"
    assert run.status is not AnalysisRunStatus.FAILED
    assert run.scores_json is not None
    assert set(run.scores_json) == {
        "technical_seo_health",
        "content_aeo_readiness",
        "ai_search_geo_readiness",
    }
    for score in run.scores_json.values():
        assert 0 <= score["value"] <= 100
    technical = run.scores_json["technical_seo_health"]
    assert any(signal["rule"] == "SEO-CANONICAL-001" for signal in technical["signals"])
    for signal in technical["signals"]:
        assert signal["evidence"]
    findings = list(db.scalars(select(Finding).where(Finding.analysis_run_id == run.id)))
    assert findings
    rules = {row.rule for row in findings}
    assert "SEO-CANONICAL-001" in rules
    for row in findings:
        assert row.evidence
        assert row.rule
        assert row.affected_resource
        assert row.confidence is not None
        assert row.risk
        assert row.will_validate
        assert row.change_worked
        assert row.rollback
        assert row.reach_input == "affected_page_count"
        assert row.impressions is None
        assert all(
            not (isinstance(item, dict) and item.get("source") == "Google Search Console")
            for item in (row.evidence or [])
        )


def test_audit_skips_retrieval_by_default(db, project, crawled_site, monkeypatch) -> None:
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("audit must not touch retrieval or its stores by default")

    monkeypatch.setattr("app.jobs.handlers.audit.retrieve", _forbidden)
    monkeypatch.setattr("app.jobs.handlers.audit.get_qdrant", _forbidden)
    monkeypatch.setattr("app.jobs.handlers.audit.get_driver", _forbidden)
    job = _run_audit(db, project.id)
    assert job.status is JobStatus.SUCCEEDED
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id)
        .order_by(AnalysisRun.id.desc())
    )
    assert run is not None
    assert run.inputs_json is not None
    assert run.inputs_json["retrieval"] is False
    assert run.inputs_json["qdrant"] is None
    assert run.inputs_json["neo4j"] is None
    capabilities = [gap["capability"] for gap in (run.gaps_json or [])]
    assert "qdrant" not in capabilities
    assert "neo4j" not in capabilities
    assert "retrieval" not in capabilities
    findings = list(db.scalars(select(Finding).where(Finding.analysis_run_id == run.id)))
    assert findings
    for row in findings:
        assert all(item.get("confidence") != "derived" for item in (row.evidence or []))


def test_qdrant_unavailable_marks_audit_partial_and_names_qdrant(
    db, project, crawled_site, stub_retrieve, monkeypatch
) -> None:
    def _down(_settings=None):
        raise VectorError("Qdrant unavailable: stopped")

    monkeypatch.setattr("app.jobs.handlers.audit.get_qdrant", _down)
    monkeypatch.setattr(
        "app.jobs.handlers.audit.get_settings",
        lambda: get_settings().model_copy(update={"audit_retrieval_enabled": True}),
    )
    job = _run_audit(db, project.id)
    assert job.status is JobStatus.SUCCEEDED
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id)
        .order_by(AnalysisRun.id.desc())
    )
    assert run is not None
    assert run.status is AnalysisRunStatus.PARTIAL
    capabilities = [gap["capability"] for gap in (run.gaps_json or [])]
    assert "qdrant" in capabilities
    assert run.inputs_json is not None
    assert run.inputs_json["qdrant"] is False
    findings = list(db.scalars(select(Finding).where(Finding.analysis_run_id == run.id)))
    assert findings


def test_audit_with_gsc_attaches_query_and_page_metrics(
    db, project, crawled_site, stub_retrieve, monkeypatch
) -> None:
    from datetime import date, datetime, timezone

    from app.models.search import SearchConsoleDimension, SearchConsoleRow
    from app.services.search_console import snapshot_from_rows

    page_url = f"{_BASE}/index.html"
    snapshot = snapshot_from_rows(
        [
            SearchConsoleRow(
                project_id=project.id,
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
                project_id=project.id,
                dimension=SearchConsoleDimension.PAGE_QUERY,
                page=page_url,
                query="crm software",
                impressions=90000,
                clicks=800,
                ctr=0.0089,
                position=8.1,
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 28),
                fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            ),
        ],
        property_url=_BASE,
    )

    def _fake_fetch(_db, _project_id, **_kwargs):
        return snapshot

    monkeypatch.setattr("app.jobs.handlers.audit.fetch_and_store", _fake_fetch)
    job = _run_audit(db, project.id)
    assert job.status is JobStatus.SUCCEEDED
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id)
        .order_by(AnalysisRun.id.desc())
    )
    assert run is not None
    assert run.inputs_json is not None
    assert run.inputs_json["search_console"] == "attached"
    findings = list(db.scalars(select(Finding).where(Finding.analysis_run_id == run.id)))
    matched = [row for row in findings if row.affected_url == page_url]
    assert matched
    for row in matched:
        assert row.impressions == 180000
        assert row.reach_input == "impressions"
        excerpts = [
            item["excerpt"]
            for item in row.evidence
            if isinstance(item, dict) and item.get("source") == "Google Search Console"
        ]
        assert excerpts
        assert "180000 impressions" in excerpts[0]
        assert "0.8% CTR" in excerpts[0]
        assert "crm software" in excerpts[0]
