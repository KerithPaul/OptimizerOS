"""Audit job (step 5.B.3).

Repository facts (if any) + site facts (if any) + rule hits → Evidence
Engine → findings persisted → scores stored.

A missing input is an explicit gap on the analysis run, not an omission.
When `AUDIT_RETRIEVAL_ENABLED` is on and Qdrant or Neo4j is down the run is
`partial` and names the missing capability. Retrieval is off by default: it
only adds derived evidence rows and is recorded as `retrieval: false` in the
run inputs. It is forbidden to emit a healthy audit built from fewer inputs
than claimed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.model import Page
from app.core.config import get_settings
from app.intelligence.repository.graph import GraphError, get_driver
from app.jobs.registry import ProgressReporter, register
from app.knowledge.evaluator import EvaluableRule, evaluate, merge_evaluable_rules
from app.models.finding import AnalysisRun, AnalysisRunStatus
from app.models.job import Job
from app.models.knowledge import OptimizationRule
from app.models.repository import CloneStatus, Repository
from app.models.website import CrawlRun, Website, WebsitePage
from app.retrieval.evidence import (
    assemble_findings,
    load_rule_meta,
    persist_findings,
)
from app.retrieval.hybrid import RetrievalError, RetrievalScope, retrieve
from app.retrieval.scoring import compute_scores_json
from app.services.search_console import (
    STATUS_FAILED,
    attach_to_findings,
    fetch_and_store,
)
from app.services.vectors import VectorError, get_qdrant


def audit(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    retrieval_enabled = get_settings().audit_retrieval_enabled
    now = datetime.now(timezone.utc)
    run = AnalysisRun(
        project_id=job.project_id,
        job_id=job.id,
        status=AnalysisRunStatus.RUNNING,
        started_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    gaps: list[dict[str, str]] = []
    try:
        report_progress("Collecting", 10, "Collecting repository and site facts")
        website, crawl_run, pages, crawl_stats, site_gaps = _site_facts(db, job.project_id)
        gaps.extend(site_gaps)

        repository, repository_facts, repo_gaps = _repository_facts(db, job.project_id)
        gaps.extend(repo_gaps)

        qdrant_ok: bool | None = None
        neo4j_ok: bool | None = None
        if retrieval_enabled:
            qdrant_ok, qdrant_gap = _probe_qdrant()
            if qdrant_gap:
                gaps.append(qdrant_gap)
            neo4j_ok, neo4j_gap = _probe_neo4j()
            if neo4j_gap:
                gaps.append(neo4j_gap)

        report_progress("Search Console", 25, "Retrieving Search Console data")
        gsc_snapshot = fetch_and_store(
            db, job.project_id, analysis_run_id=run.id
        )
        if gsc_snapshot.status.status == STATUS_FAILED:
            gaps.append(
                {
                    "capability": "search_console",
                    "detail": gsc_snapshot.status.detail,
                }
            )

        run.crawl_run_id = crawl_run.id if crawl_run is not None else None
        run.repository_id = repository.id if repository is not None else None
        sitemap_url_count = _sitemap_url_count(crawl_stats)
        if (
            pages
            and sitemap_url_count is not None
            and sitemap_url_count >= 10
            and len(pages) <= 2
        ):
            gaps.append(
                {
                    "capability": "crawl_coverage",
                    "detail": (
                        f"audit evaluated {len(pages)} page(s) but the sitemap lists "
                        f"{sitemap_url_count} URLs — recrawl before trusting site-wide findings"
                    ),
                }
            )
        run.inputs_json = {
            "repository": repository_facts is not None,
            "website": website is not None,
            "crawl_run_id": crawl_run.id if crawl_run is not None else None,
            "page_count": len(pages),
            "sitemap_url_count": sitemap_url_count,
            "retrieval": retrieval_enabled,
            "qdrant": qdrant_ok,
            "neo4j": neo4j_ok,
            "search_console": gsc_snapshot.status.status,
            "search_console_display": gsc_snapshot.status.display,
            "search_console_detail": gsc_snapshot.status.detail,
        }
        db.commit()

        report_progress("Evaluating", 40, f"Evaluating rules against {len(pages)} pages")
        rules = _evaluable_rules(db)
        evaluation = evaluate(
            pages,
            crawl_stats=crawl_stats,
            repository_facts=repository_facts,
            rules=rules,
        )

        retrieved_by_rule: dict[str, list] = {}
        if retrieval_enabled:
            report_progress("Retrieving", 60, "Retrieving knowledge and project evidence")
            if qdrant_ok or neo4j_ok:
                retrieved_by_rule, retrieval_gaps = _retrieve_for_hits(
                    evaluation.hits,
                    project_id=job.project_id,
                    website_id=website.id if website is not None else None,
                    repository_id=repository.id if repository is not None else None,
                )
                gaps.extend(retrieval_gaps)
            else:
                gaps.append(
                    {
                        "capability": "retrieval",
                        "detail": "skipped because qdrant and neo4j are unavailable",
                    }
                )

        report_progress("Assembling", 80, f"Assembling findings from {len(evaluation.hits)} rule hits")
        meta = load_rule_meta(db)
        if repository_facts is not None:
            actionability = "code_change"
        elif website is not None and website.platform == "wordpress":
            actionability = "code_or_platform_change"
        else:
            actionability = "recommend_only"
        records = assemble_findings(
            evaluation.hits,
            meta,
            retrieved_by_rule=retrieved_by_rule,
            actionability=actionability,
            repository_facts=repository_facts,
        )

        report_progress("Persisting", 90, f"Persisting {len(records)} findings")
        rows = persist_findings(db, run, records)
        attach_to_findings(rows, gsc_snapshot)

        unique_gaps = _dedupe_gaps(gaps)
        run.gaps_json = unique_gaps
        if run.inputs_json is not None:
            run.inputs_json = {
                **run.inputs_json,
                "rule_hit_count": len(evaluation.hits),
                "finding_count": len(rows),
            }
        run.scores_json = compute_scores_json(rows)
        if unique_gaps:
            run.status = AnalysisRunStatus.PARTIAL
        else:
            run.status = AnalysisRunStatus.SUCCEEDED
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        report_progress(
            "Finishing",
            100,
            f"audit={run.status.value} findings={len(records)}"
            + (f" gaps={[item['capability'] for item in unique_gaps]}" if unique_gaps else ""),
        )
    except Exception as exc:
        run.status = AnalysisRunStatus.FAILED
        run.error = str(exc)
        run.gaps_json = _dedupe_gaps(gaps)
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise


def _site_facts(
    db: Session, project_id: int
) -> tuple[Website | None, CrawlRun | None, list[Page], dict[str, Any] | None, list[dict[str, str]]]:
    gaps: list[dict[str, str]] = []
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    if website is None:
        gaps.append({"capability": "website", "detail": "no website attached"})
        return None, None, [], None, gaps

    crawl_run = db.scalar(
        select(CrawlRun)
        .where(CrawlRun.website_id == website.id)
        .order_by(CrawlRun.id.desc())
    )
    if crawl_run is None:
        gaps.append({"capability": "site_facts", "detail": "no crawl run"})
        return website, None, [], None, gaps

    rows = list(
        db.scalars(select(WebsitePage).where(WebsitePage.crawl_run_id == crawl_run.id))
    )
    pages: list[Page] = []
    for row in rows:
        if not row.model_json:
            continue
        pages.append(Page.model_validate(row.model_json))
    if not pages:
        gaps.append({"capability": "site_facts", "detail": "crawl run has no page models"})

    stats = crawl_run.stats_json or {}
    crawl_stats: dict[str, Any] | None
    nested = stats.get("crawl") if isinstance(stats, dict) else None
    if isinstance(nested, dict):
        crawl_stats = nested
    elif isinstance(stats, dict) and stats:
        crawl_stats = stats
    else:
        crawl_stats = None
    return website, crawl_run, pages, crawl_stats, gaps


def _repository_facts(
    db: Session, project_id: int
) -> tuple[Repository | None, dict[str, Any] | None, list[dict[str, str]]]:
    gaps: list[dict[str, str]] = []
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if repository is None:
        gaps.append({"capability": "repository", "detail": "no repository attached"})
        return None, None, gaps
    if repository.clone_status != CloneStatus.CLONED:
        gaps.append(
            {
                "capability": "repository",
                "detail": f"clone_status={repository.clone_status.value}",
            }
        )
        return repository, None, gaps
    facts = {
        "architecture_profile": repository.architecture_profile,
        "cloned_commit_hash": repository.cloned_commit_hash,
        "last_indexed_commit": repository.last_indexed_commit,
        "url": repository.url,
    }
    return repository, facts, gaps


def _probe_qdrant() -> tuple[bool, dict[str, str] | None]:
    try:
        get_qdrant()
        return True, None
    except VectorError as exc:
        return False, {"capability": "qdrant", "detail": str(exc)}


def _probe_neo4j() -> tuple[bool, dict[str, str] | None]:
    try:
        get_driver()
        return True, None
    except GraphError as exc:
        return False, {"capability": "neo4j", "detail": str(exc)}


def _evaluable_rules(db: Session) -> list[EvaluableRule]:
    rows = list(db.scalars(select(OptimizationRule)))
    latest: dict[str, OptimizationRule] = {}
    for row in rows:
        current = latest.get(row.rule_id)
        if current is None or row.version > current.version:
            latest[row.rule_id] = row
    db_rules = [EvaluableRule.from_row(row) for row in latest.values()]
    return merge_evaluable_rules(db_rules)


def _sitemap_url_count(crawl_stats: dict[str, Any] | None) -> int | None:
    if not crawl_stats:
        return None
    sitemap = crawl_stats.get("sitemap")
    if not isinstance(sitemap, dict):
        return None
    raw = sitemap.get("page_url_count")
    if isinstance(raw, int):
        return raw
    return None


def _retrieve_for_hits(
    hits,
    *,
    project_id: int,
    website_id: int | None,
    repository_id: int | None,
) -> tuple[dict[str, list], list[dict[str, str]]]:
    gaps: list[dict[str, str]] = []
    retrieved: dict[str, list] = {}
    settings = get_settings()
    for rule_id in dict.fromkeys(hit.rule_id for hit in hits):
        scope = RetrievalScope(
            project_id=project_id,
            website_id=website_id,
            repository_id=repository_id,
            rule_id=rule_id,
        )
        try:
            result = retrieve(rule_id, scope=scope, settings=settings)
        except RetrievalError as exc:
            gaps.append({"capability": _gap_capability(str(exc)), "detail": str(exc)})
            break
        retrieved[rule_id] = result.items
        for item in result.gaps:
            gaps.append({"capability": _gap_capability(item), "detail": item})
    return retrieved, gaps


def _gap_capability(raw: str) -> str:
    lower = raw.lower()
    if "qdrant" in lower:
        return "qdrant"
    if "neo4j" in lower or "graph" in lower:
        return "neo4j"
    return "retrieval"


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


register("audit", audit)
