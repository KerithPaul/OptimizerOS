"""Persist golden project E across MySQL, Qdrant, and Neo4j (step 3.D.1)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.website.extract import extract_page
from app.intelligence.website.graph import delete_website_graph, orphan_pages
from app.intelligence.website.persist import persist_crawl
from app.jobs.queue import enqueue, get_redis
from app.models.project import Project
from app.models.user import User
from app.models.website import CrawlRun, CrawlRunStatus, PlatformConnection, Website, WebsitePage
from app.services.vectors import delete_page_content, scroll_page_content
from app.worker import run_job
from app.intelligence.website.crawler import CrawlResult
from app.intelligence.website.pipeline import AnalyzedSite
from app.intelligence.website.robots import RobotsState
from app.intelligence.website.sitemap import SitemapEvidence

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
        user = User(email="persist-test@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name="website-persist-test", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj
    db.rollback()
    row = db.get(Project, proj.id)
    if row is not None:
        db.delete(row)
        db.commit()


def test_orphan_page_is_discoverable_in_all_three_stores(db, project) -> None:
    website = Website(project_id=project.id, url=f"{_BASE}/index.html", platform="url_only")
    db.add(website)
    db.commit()
    db.refresh(website)
    run = CrawlRun(
        website_id=website.id,
        project_id=project.id,
        status=CrawlRunStatus.RUNNING,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    pages = _golden_pages()
    try:
        stats = persist_crawl(
            db,
            project_id=project.id,
            website_id=website.id,
            crawl_run=run,
            pages=pages,
            home_url=f"{_BASE}/index.html",
            lighthouse=[
                {
                    "url": f"{_BASE}/index.html",
                    "state": "observed",
                    "provenance": "lab_signal_not_ranking",
                    "provenance_label": "lab signal, not ranking",
                    "categories": {"seo": 0.1},
                    "audits": [],
                    "message": None,
                }
            ],
        )
        assert stats.mysql_pages == 5
        stored = list(
            db.scalars(select(WebsitePage).where(WebsitePage.crawl_run_id == run.id))
        )
        assert len(stored) == 5
        assert any(row.title == "Golden E" for row in stored)
        assert any(
            row.model_json and row.model_json.get("canonical") is None
            for row in stored
            if row.url.endswith("/about.html")
        )

        vectors = scroll_page_content(project.id, website.id)
        assert len(vectors) == 5
        assert all(row["source_type"] == "page" for row in vectors)
        assert any(row["url"].endswith("/orphan.html") for row in vectors)

        orphans = orphan_pages(project.id, website.id)
        orphan_urls = [row["url"] for row in orphans]
        assert any(url.endswith("/orphan.html") for url in orphan_urls)
        assert not any(url.endswith("/index.html") for url in orphan_urls)

        db.refresh(run)
        signals = (run.stats_json or {}).get("lighthouse") or []
        assert signals
        assert signals[0]["provenance"] == "lab_signal_not_ranking"
        assert signals[0]["provenance_label"] == "lab signal, not ranking"
    finally:
        delete_website_graph(project.id, website.id)
        delete_page_content(project.id, website.id)


def test_website_crawl_job_partial_is_job_success(db, project, monkeypatch) -> None:
    from app.models.job import Job, JobStatus

    website = Website(project_id=project.id, url=f"{_BASE}/index.html", platform="url_only")
    db.add(website)
    db.flush()
    db.add(
        PlatformConnection(
            website_id=website.id,
            project_id=project.id,
            platform="url_only",
            auth_type="none",
            capabilities_json={
                "platform": "url_only",
                "source_access": False,
                "content_access": True,
                "metadata_access": True,
                "theme_source": False,
                "seo_modification": "none",
                "aeo_modification": "none",
                "geo_modification": "none",
                "theme_modification": "none",
                "automatic_rollback": "none",
                "snapshot": False,
            },
        )
    )
    db.commit()
    db.refresh(website)

    pages = _golden_pages()[:2]

    def fake_analyze(start_url, settings=None):
        return AnalyzedSite(
            start_url=start_url,
            crawl=CrawlResult(
                status="partial",
                cap_reason="CRAWL_MAX_URLS",
                pages=[],
                skipped=[],
                failed_urls=[],
                robots=RobotsState(state="unavailable", http_status=0, raw_text=None),
                sitemap=SitemapEvidence(page_urls=[], records=[], sampled_outcomes=[]),
            ),
            pages=pages,
            lighthouse=[
                {
                    "url": pages[0].url,
                    "state": "unavailable",
                    "provenance": "lab_signal_not_ranking",
                    "provenance_label": "lab signal, not ranking",
                    "categories": {},
                    "audits": [],
                    "message": "test",
                }
            ],
        )

    monkeypatch.setattr("app.jobs.handlers.website_crawl.analyze_site", fake_analyze)

    settings = get_settings()
    job = enqueue(db, project.id, "website_crawl", settings)
    popped = get_redis(settings).brpop([settings.job_queue_key], timeout=5)
    assert popped is not None
    try:
        worker_db = SessionLocal()
        try:
            run_job(worker_db, job.id, settings)
        finally:
            worker_db.close()
        db.rollback()
        finished = db.get(Job, job.id)
        assert finished is not None
        assert finished.status == JobStatus.SUCCEEDED
        run = db.scalar(select(CrawlRun).where(CrawlRun.website_id == website.id))
        assert run is not None
        assert run.status == CrawlRunStatus.PARTIAL
        assert run.cap_reason == "CRAWL_MAX_URLS"
        stored = list(db.scalars(select(WebsitePage).where(WebsitePage.website_id == website.id)))
        assert len(stored) == 2
    finally:
        delete_website_graph(project.id, website.id)
        delete_page_content(project.id, website.id)
        row = db.get(Job, job.id)
        if row is not None:
            db.delete(row)
            db.commit()
