"""Persist a crawl across MySQL, Qdrant, and Neo4j (step 3.D.1).

MySQL stores the Common Website Model per page. Qdrant `page_content`
holds embeddings of page text. Neo4j holds Page / URL / LINKS_TO.
A Qdrant or Neo4j failure is raised — never reported as a successful persist.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.connectors.model import Page
from app.core.config import Settings, get_settings
from app.intelligence.website.graph import write_website_graph
from app.models.website import CrawlRun, WebsitePage
from app.services.vectors import PageContent, IndexStats, index_page_content

logger = logging.getLogger("architectos.intelligence.website.persist")


@dataclass
class PersistStats:
    mysql_pages: int
    graph_nodes: int
    embedded: int
    reused: int
    deleted: int


def persist_crawl(
    db: Session,
    *,
    project_id: int,
    website_id: int,
    crawl_run: CrawlRun,
    pages: list[Page],
    home_url: str,
    observations: dict[str, dict] | None = None,
    lighthouse: list[dict] | None = None,
    renders: list[dict] | None = None,
    crawl_stats: dict | None = None,
    settings: Settings | None = None,
) -> PersistStats:
    """Write pages to all three stores and record crawl_run.stats_json."""

    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    rows: list[WebsitePage] = []
    for page in pages:
        row = WebsitePage(
            website_id=website_id,
            crawl_run_id=crawl_run.id,
            url=page.url,
            status_code=page.status_code,
            title=page.title,
            canonical=page.canonical,
            model_json=page.model_dump(mode="json"),
            updated_at=now,
        )
        db.add(row)
        rows.append(row)
    db.flush()

    documents = [_page_content(row.id, page) for row, page in zip(rows, pages, strict=True)]
    vector_stats: IndexStats = index_page_content(
        project_id, website_id, documents, settings=settings
    )
    graph_nodes = write_website_graph(
        project_id,
        website_id,
        pages,
        home_url=home_url,
        crawl_run_id=crawl_run.id,
        settings=settings,
    )

    stats = {
        "page_count": len(pages),
        "embedded": vector_stats.embedded,
        "reused": vector_stats.reused,
        "graph_nodes": graph_nodes,
        "observations": observations or {},
        "lighthouse": lighthouse or [],
        "renders": renders or [],
        "crawl": crawl_stats or {},
    }
    crawl_run.stats_json = stats
    db.commit()

    result = PersistStats(
        mysql_pages=len(rows),
        graph_nodes=graph_nodes,
        embedded=vector_stats.embedded,
        reused=vector_stats.reused,
        deleted=vector_stats.deleted,
    )
    logger.info(
        "crawl persisted (project_id=%s, website_id=%s, pages=%s, graph_nodes=%s)",
        project_id,
        website_id,
        result.mysql_pages,
        result.graph_nodes,
    )
    return result


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_content(page_id: int, page: Page) -> PageContent:
    text = " ".join(part for part in (page.title, page.content) if part) or page.url
    return PageContent(
        page_id=page_id,
        url=page.url,
        title=page.title,
        text=text,
        language=page.language,
        content_hash=content_hash(text),
    )
