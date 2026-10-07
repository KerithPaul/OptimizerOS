"""URL-only crawl job (step 3.D): discover, extract, render, Lighthouse, persist.

A crawl that hits a cap records `crawl_runs.status=partial` and the job
still succeeds. A fetch/persist failure raises so the worker marks the
job `failed`. Playwright and Lighthouse failures are recorded states.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.capabilities import WORDPRESS_PLATFORM
from app.connectors.wordpress.auth import WordPressAuthError, wordpress_connection
from app.connectors.wordpress.connector import WordPressConnector
from app.connectors.wordpress.rest import WordPressApiError
from app.core.config import get_settings
from app.intelligence.website.pipeline import analyze_site
from app.intelligence.website.persist import persist_crawl
from app.jobs.registry import ProgressReporter, register
from app.models.job import Job
from app.models.website import CrawlRun, CrawlRunStatus, Website


def website_crawl(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    website = db.scalar(select(Website).where(Website.project_id == job.project_id))
    if website is None:
        raise RuntimeError("no website attached to this project")

    settings = get_settings()
    now = datetime.now(timezone.utc)
    run = CrawlRun(
        website_id=website.id,
        project_id=job.project_id,
        status=CrawlRunStatus.RUNNING,
        started_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    report_progress("Crawling", 10, f"Crawling {website.url}")
    try:
        if website.platform == WORDPRESS_PLATFORM:
            connection = wordpress_connection(db, job.project_id)
            if connection is None or not connection.credentials_encrypted:
                raise RuntimeError("WordPress authentication failure: application password is not stored")
            connector = WordPressConnector.from_connection(
                connection, website.url, settings=settings
            )
            connector.authenticate()
            pages = connector.discover()
            from types import SimpleNamespace

            analyzed = SimpleNamespace(
                pages=pages,
                crawl=SimpleNamespace(
                    status="succeeded",
                    cap_reason=None,
                    pages=[],
                    skipped=[],
                    failed_urls=[],
                    crawled_not_in_sitemap=[],
                    robots=SimpleNamespace(
                        state="skipped",
                        http_status=None,
                        disallows_us=False,
                        ai_bot_disallows=[],
                        sitemap_urls=[],
                    ),
                    sitemap=SimpleNamespace(page_urls=[], records=[]),
                ),
                observations={},
                lighthouse=[],
                renders=[],
            )
        else:
            analyzed = analyze_site(website.url, settings=settings)
    except WordPressAuthError as exc:
        run.status = CrawlRunStatus.FAILED
        run.error = f"WordPress authentication failure: {exc}"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise RuntimeError(run.error) from exc
    except WordPressApiError as exc:
        run.status = CrawlRunStatus.FAILED
        run.error = f"WordPress API failure: {exc}"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise RuntimeError(run.error) from exc
    except Exception as exc:
        run.status = CrawlRunStatus.FAILED
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise

    crawl = analyzed.crawl
    report_progress(
        "Extracting",
        55,
        f"extracted={len(analyzed.pages)} status={crawl.status}",
    )
    if analyzed.renders:
        report_progress("Rendering", 70, f"rendered={len(analyzed.renders)}")
    if analyzed.lighthouse:
        report_progress(
            "Lighthouse",
            80,
            f"lab_signals={len(analyzed.lighthouse)}",
        )

    if crawl.status == "failed" and not analyzed.pages:
        run.status = CrawlRunStatus.FAILED
        run.cap_reason = crawl.cap_reason
        run.error = "crawl produced no pages"
        run.stats_json = _crawl_stats(analyzed)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise RuntimeError("crawl produced no pages")

    report_progress("Persisting", 90, f"Writing {len(analyzed.pages)} pages")
    try:
        persist_crawl(
            db,
            project_id=job.project_id,
            website_id=website.id,
            crawl_run=run,
            pages=analyzed.pages,
            home_url=website.url,
            observations=analyzed.observations,
            lighthouse=analyzed.lighthouse,
            renders=analyzed.renders,
            crawl_stats=_crawl_stats(analyzed),
            settings=settings,
        )
    except Exception as exc:
        run.status = CrawlRunStatus.FAILED
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise

    if crawl.status == "partial":
        run.status = CrawlRunStatus.PARTIAL
        run.cap_reason = crawl.cap_reason
    else:
        run.status = CrawlRunStatus.SUCCEEDED
        run.cap_reason = crawl.cap_reason
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    report_progress(
        "Finishing",
        100,
        f"crawl={run.status.value} pages={len(analyzed.pages)}"
        + (f" cap={run.cap_reason}" if run.cap_reason else ""),
    )


def _crawl_stats(analyzed) -> dict:
    crawl = analyzed.crawl
    return {
        "status": crawl.status,
        "cap_reason": crawl.cap_reason,
        "page_count": len(crawl.pages),
        "skipped": [
            {"url": item.url, "reason": item.reason, "depth": item.depth}
            for item in crawl.skipped
        ],
        "failed_urls": list(crawl.failed_urls),
        "crawled_not_in_sitemap": list(crawl.crawled_not_in_sitemap),
        "robots": {
            "state": crawl.robots.state,
            "http_status": crawl.robots.http_status,
            "disallows_us": crawl.robots.disallows_us,
            "ai_bot_disallows": list(crawl.robots.ai_bot_disallows),
            "sitemap_urls": list(crawl.robots.sitemap_urls),
        },
        "sitemap": {
            "page_url_count": len(crawl.sitemap.page_urls),
            "records": [
                {
                    "url": record.url,
                    "status": record.status,
                    "parse_state": record.parse_state,
                    "kind": record.kind,
                }
                for record in crawl.sitemap.records
            ],
        },
        "llms_txt": analyzed.llms_txt,
    }


register("website_crawl", website_crawl)
