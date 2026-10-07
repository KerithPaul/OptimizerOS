"""Before/after measurement snapshots (step 11.5).

When data is available, compare before vs after. Metrics `[SPEC]`:
CTR, impressions, clicks, position, crawlability, technical errors,
answer/entity/citation retrieval benchmarks, structured-data validity,
page performance.

A missing metric is recorded as unavailable. Numbers are never invented.
Correlation is not treated as causation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finding import Finding
from app.models.search import SearchConsoleDimension, SearchConsoleRow
from app.models.website import CrawlRun, Website, WebsitePage
from app.services.search_console import (
    STATUS_ATTACHED,
    SearchConsoleSnapshot,
    canonical_page_key,
    fetch_and_store,
)

UNAVAILABLE = "unavailable"
CAUSATION_NOT_CLAIMED = "not_claimed"
CAUSATION_NOTE = (
    "Observed metric movement is correlation, not evidence that the change caused it."
)

METRIC_KEYS = (
    "ctr",
    "impressions",
    "clicks",
    "position",
    "crawlability",
    "technical_errors",
    "answer_retrieval_benchmark",
    "entity_recognition_benchmark",
    "citation_retrieval_benchmark",
    "structured_data_validity",
    "page_performance",
)


def unavailable_metric(reason: str) -> dict[str, Any]:
    return {"status": UNAVAILABLE, "value": None, "detail": reason}


def capture_metrics(
    db: Session,
    project_id: int,
    *,
    finding_ids: list[str] | None = None,
    urls: list[str] | None = None,
    snapshot: SearchConsoleSnapshot | None = None,
    fetch_gsc: bool = False,
) -> dict[str, Any]:
    """Build a measurement snapshot. GSC is fetched only when asked."""

    gsc_snapshot = snapshot
    if fetch_gsc and gsc_snapshot is None:
        gsc_snapshot = fetch_and_store(db, project_id)
    gsc_metrics = _gsc_metrics(db, project_id, gsc_snapshot, urls=urls)
    crawl_metrics = _crawl_metrics(db, project_id)
    onsite = _onsite_metrics(db, project_id, finding_ids=finding_ids)
    captured_at = datetime.now(timezone.utc).isoformat()
    return {
        "captured_at": captured_at,
        "causation": CAUSATION_NOT_CLAIMED,
        "causation_note": CAUSATION_NOTE,
        "metrics": {
            "ctr": gsc_metrics["ctr"],
            "impressions": gsc_metrics["impressions"],
            "clicks": gsc_metrics["clicks"],
            "position": gsc_metrics["position"],
            "crawlability": crawl_metrics["crawlability"],
            "technical_errors": crawl_metrics["technical_errors"],
            "answer_retrieval_benchmark": unavailable_metric(
                "answer retrieval benchmark is not measured in this phase"
            ),
            "entity_recognition_benchmark": unavailable_metric(
                "entity recognition benchmark is not measured in this phase"
            ),
            "citation_retrieval_benchmark": unavailable_metric(
                "citation retrieval benchmark is not measured in this phase"
            ),
            "structured_data_validity": onsite["structured_data_validity"],
            "page_performance": onsite["page_performance"],
        },
        "search_console": (
            gsc_snapshot.status.display
            if gsc_snapshot is not None
            else "Search Console: unavailable"
        ),
        "urls": urls or [],
        "finding_ids": finding_ids or [],
    }


def compare_metrics(baseline: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    """Observed deltas only. Never a causal claim."""

    baseline_metrics = baseline.get("metrics") or {}
    treatment_metrics = treatment.get("metrics") or {}
    observed: dict[str, Any] = {}
    for key in METRIC_KEYS:
        before = baseline_metrics.get(key) or unavailable_metric("missing baseline")
        after = treatment_metrics.get(key) or unavailable_metric("missing treatment")
        if before.get("status") == UNAVAILABLE or after.get("status") == UNAVAILABLE:
            observed[key] = {
                "status": UNAVAILABLE,
                "detail": before.get("detail") or after.get("detail"),
                "before": before,
                "after": after,
            }
            continue
        before_value = before.get("value")
        after_value = after.get("value")
        delta = None
        if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
            delta = after_value - before_value
        observed[key] = {
            "status": "observed",
            "before": before_value,
            "after": after_value,
            "delta": delta,
        }
    return {
        "causation": CAUSATION_NOT_CLAIMED,
        "causation_note": CAUSATION_NOTE,
        "observed_delta": observed,
    }


def _gsc_metrics(
    db: Session,
    project_id: int,
    snapshot: SearchConsoleSnapshot | None,
    *,
    urls: list[str] | None,
) -> dict[str, dict[str, Any]]:
    if snapshot is None or snapshot.status.status != STATUS_ATTACHED:
        reason = (
            snapshot.status.detail
            if snapshot is not None
            else "no Search Console credentials"
        )
        missing = unavailable_metric(reason)
        return {
            "ctr": missing,
            "impressions": missing,
            "clicks": missing,
            "position": missing,
        }
    rows = [
        row
        for row in snapshot.rows
        if row.dimension is SearchConsoleDimension.PAGE
    ]
    if not rows:
        rows = list(
            db.scalars(
                select(SearchConsoleRow).where(
                    SearchConsoleRow.project_id == project_id,
                    SearchConsoleRow.dimension == SearchConsoleDimension.PAGE,
                )
            )
        )
    if urls:
        keys = {canonical_page_key(url) for url in urls}
        rows = [row for row in rows if row.page and canonical_page_key(row.page) in keys]
    if not rows:
        missing = unavailable_metric("no matching Search Console page rows")
        return {
            "ctr": missing,
            "impressions": missing,
            "clicks": missing,
            "position": missing,
        }
    impressions = sum(row.impressions for row in rows)
    clicks = sum(row.clicks for row in rows)
    ctr = (clicks / impressions) if impressions else 0.0
    weighted_position = (
        sum(row.position * row.impressions for row in rows) / impressions
        if impressions
        else 0.0
    )
    return {
        "impressions": {"status": "measured", "value": impressions, "source": "google_search_console"},
        "clicks": {"status": "measured", "value": clicks, "source": "google_search_console"},
        "ctr": {"status": "measured", "value": ctr, "source": "google_search_console"},
        "position": {
            "status": "measured",
            "value": weighted_position,
            "source": "google_search_console",
        },
    }


def _crawl_metrics(db: Session, project_id: int) -> dict[str, dict[str, Any]]:
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    if website is None:
        missing = unavailable_metric("no website attached")
        return {"crawlability": missing, "technical_errors": missing}
    crawl = db.scalar(
        select(CrawlRun)
        .where(CrawlRun.website_id == website.id)
        .order_by(CrawlRun.id.desc())
    )
    if crawl is None:
        missing = unavailable_metric("no crawl run")
        return {"crawlability": missing, "technical_errors": missing}
    pages = list(
        db.scalars(select(WebsitePage).where(WebsitePage.crawl_run_id == crawl.id))
    )
    stats = crawl.stats_json if isinstance(crawl.stats_json, dict) else {}
    nested = stats.get("crawl") if isinstance(stats.get("crawl"), dict) else stats
    failed = nested.get("failed_urls") if isinstance(nested, dict) else None
    failed_count = len(failed) if isinstance(failed, list) else 0
    error_status = 0
    for page in pages:
        if page.status_code is not None and page.status_code >= 400:
            error_status += 1
    fetched = len(pages)
    attempted = fetched + failed_count
    crawlability = (fetched / attempted) if attempted else 0.0
    return {
        "crawlability": {
            "status": "measured",
            "value": crawlability,
            "source": "crawl",
            "fetched": fetched,
            "attempted": attempted,
        },
        "technical_errors": {
            "status": "measured",
            "value": failed_count + error_status,
            "source": "crawl",
            "failed_urls": failed_count,
            "error_status_pages": error_status,
        },
    }


def _onsite_metrics(
    db: Session, project_id: int, *, finding_ids: list[str] | None
) -> dict[str, dict[str, Any]]:
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    structured = unavailable_metric("no crawl pages to inspect structured data")
    performance = unavailable_metric("no Lighthouse lab signal")
    if website is not None:
        crawl = db.scalar(
            select(CrawlRun)
            .where(CrawlRun.website_id == website.id)
            .order_by(CrawlRun.id.desc())
        )
        if crawl is not None:
            pages = list(
                db.scalars(select(WebsitePage).where(WebsitePage.crawl_run_id == crawl.id))
            )
            total = 0
            invalid = 0
            for page in pages:
                model = page.model_json if isinstance(page.model_json, dict) else {}
                blocks = model.get("structured_data") or []
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    total += 1
                    if isinstance(block, dict) and block.get("parse_error"):
                        invalid += 1
            if total:
                structured = {
                    "status": "measured",
                    "value": (total - invalid) / total,
                    "source": "common_website_model",
                    "blocks": total,
                    "invalid": invalid,
                }
            else:
                structured = unavailable_metric("no structured-data blocks on crawled pages")
            stats = crawl.stats_json if isinstance(crawl.stats_json, dict) else {}
            lighthouse = stats.get("lighthouse")
            scores: list[float] = []
            if isinstance(lighthouse, list):
                for signal in lighthouse:
                    if not isinstance(signal, dict):
                        continue
                    categories = signal.get("categories") or {}
                    if isinstance(categories, dict):
                        perf = categories.get("performance")
                        if isinstance(perf, (int, float)):
                            scores.append(float(perf))
                    if signal.get("state") not in {None, "ok", "succeeded"} and not scores:
                        continue
            if scores:
                performance = {
                    "status": "measured",
                    "value": sum(scores) / len(scores),
                    "source": "lighthouse_lab_signal_not_ranking",
                }
    if finding_ids:
        # Presence of structured-data findings is additional evidence, not a
        # substitute for measured blocks above.
        _ = list(
            db.scalars(
                select(Finding).where(
                    Finding.project_id == project_id,
                    Finding.finding_id.in_(finding_ids),
                )
            )
        )
    return {
        "structured_data_validity": structured,
        "page_performance": performance,
    }
