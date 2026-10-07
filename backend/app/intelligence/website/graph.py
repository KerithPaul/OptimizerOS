"""Neo4j write of website Page / URL / LINKS_TO (step 3.D.1).

Keyed by `project_id` + `website_id` + URL so a re-crawl MERGEs rather
than duplicating. Only internal links become LINKS_TO — that graph is
what later makes orphan-page and internal-linking analysis possible.
"""

from __future__ import annotations

import logging

from neo4j.exceptions import Neo4jError

from app.connectors.model import Page
from app.core.config import Settings
from app.intelligence.repository.graph import GraphError, get_driver

logger = logging.getLogger("architectos.intelligence.website.graph")

_CONSTRAINT_LABELS = ("Website", "Page", "URL")


def website_stable_id(project_id: int, website_id: int) -> str:
    return f"{project_id}:website:{website_id}"


def page_stable_id(project_id: int, website_id: int, url: str) -> str:
    return f"{project_id}:page:{website_id}:{url}"


def url_stable_id(project_id: int, website_id: int, url: str) -> str:
    return f"{project_id}:url:{website_id}:{url}"


def write_website_graph(
    project_id: int,
    website_id: int,
    pages: list[Page],
    *,
    home_url: str,
    crawl_run_id: int | None = None,
    settings: Settings | None = None,
) -> int:
    """Replace this website's Page/URL graph with the latest crawl."""

    driver = get_driver(settings)
    try:
        with driver.session() as session:
            session.execute_write(_ensure_constraints)
            session.execute_write(
                _replace_website,
                project_id,
                website_id,
                home_url,
                crawl_run_id,
                pages,
            )
            count = session.execute_read(_count_nodes, project_id, website_id)
    except GraphError:
        raise
    except Neo4jError as exc:
        raise GraphError(f"Neo4j write failed: {exc}") from exc
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc
    logger.info(
        "website graph write (project_id=%s, website_id=%s, nodes=%s, pages=%s)",
        project_id,
        website_id,
        count,
        len(pages),
    )
    return count


def orphan_pages(
    project_id: int,
    website_id: int,
    *,
    settings: Settings | None = None,
) -> list[dict]:
    """Pages with no inbound internal LINKS_TO from a different URL."""

    driver = get_driver(settings)
    try:
        with driver.session() as session:
            records = session.run(
                """
                MATCH (p:Page {project_id: $project_id, website_id: $website_id})
                      -[:REPRESENTS]->(u:URL)
                OPTIONAL MATCH (other:URL {project_id: $project_id, website_id: $website_id})
                         -[:LINKS_TO]->(u)
                WHERE other.stable_id <> u.stable_id
                WITH p, u, count(other) AS inbound
                WHERE inbound = 0
                RETURN p.url AS url, p.title AS title
                ORDER BY p.url
                """,
                project_id=project_id,
                website_id=website_id,
            )
            return [dict(record) for record in records]
    except GraphError:
        raise
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc


def delete_website_graph(
    project_id: int,
    website_id: int,
    settings: Settings | None = None,
) -> None:
    """Test helper: remove one website's nodes."""

    driver = get_driver(settings)
    with driver.session() as session:
        session.run(
            """
            MATCH (n {project_id: $project_id, website_id: $website_id})
            DETACH DELETE n
            """,
            project_id=project_id,
            website_id=website_id,
        )


def node_count(
    project_id: int,
    website_id: int,
    settings: Settings | None = None,
) -> int:
    driver = get_driver(settings)
    try:
        with driver.session() as session:
            return session.execute_read(_count_nodes, project_id, website_id)
    except Exception as exc:
        raise GraphError(f"Neo4j unavailable: {exc}") from exc


def _ensure_constraints(tx) -> None:
    for label in _CONSTRAINT_LABELS:
        tx.run(
            f"CREATE CONSTRAINT {label.lower()}_stable_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.stable_id IS UNIQUE"
        )


def _replace_website(
    tx,
    project_id: int,
    website_id: int,
    home_url: str,
    crawl_run_id: int | None,
    pages: list[Page],
) -> None:
    tx.run(
        """
        MATCH (n {project_id: $project_id, website_id: $website_id})
        DETACH DELETE n
        """,
        project_id=project_id,
        website_id=website_id,
    )
    website_sid = website_stable_id(project_id, website_id)
    tx.run(
        """
        MERGE (w:Website {stable_id: $stable_id})
        SET w.project_id = $project_id,
            w.website_id = $website_id,
            w.url = $home_url,
            w.kind = 'Website'
        """,
        stable_id=website_sid,
        project_id=project_id,
        website_id=website_id,
        home_url=home_url,
    )

    seen_urls: set[str] = set()
    for page in pages:
        _merge_url(tx, project_id, website_id, page.url)
        seen_urls.add(page.url)
        page_sid = page_stable_id(project_id, website_id, page.url)
        url_sid = url_stable_id(project_id, website_id, page.url)
        tx.run(
            """
            MERGE (p:Page {stable_id: $page_sid})
            SET p.project_id = $project_id,
                p.website_id = $website_id,
                p.url = $url,
                p.title = $title,
                p.canonical = $canonical,
                p.status_code = $status_code,
                p.kind = 'Page',
                p.crawl_run_id = $crawl_run_id
            WITH p
            MATCH (w:Website {stable_id: $website_sid})
            MATCH (u:URL {stable_id: $url_sid})
            MERGE (w)-[:HAS_PAGE]->(p)
            MERGE (p)-[:REPRESENTS]->(u)
            """,
            page_sid=page_sid,
            website_sid=website_sid,
            url_sid=url_sid,
            project_id=project_id,
            website_id=website_id,
            url=page.url,
            title=page.title,
            canonical=page.canonical,
            status_code=page.status_code,
            crawl_run_id=crawl_run_id,
        )
        for link in page.links:
            if link.internal is not True:
                continue
            if not link.href or link.href == page.url:
                continue
            if link.href not in seen_urls:
                _merge_url(tx, project_id, website_id, link.href)
                seen_urls.add(link.href)
            tx.run(
                """
                MATCH (a:URL {stable_id: $from_id})
                MATCH (b:URL {stable_id: $to_id})
                MERGE (a)-[:LINKS_TO]->(b)
                """,
                from_id=url_stable_id(project_id, website_id, page.url),
                to_id=url_stable_id(project_id, website_id, link.href),
            )


def _merge_url(tx, project_id: int, website_id: int, url: str) -> None:
    tx.run(
        """
        MERGE (u:URL {stable_id: $stable_id})
        SET u.project_id = $project_id,
            u.website_id = $website_id,
            u.url = $url,
            u.kind = 'URL'
        """,
        stable_id=url_stable_id(project_id, website_id, url),
        project_id=project_id,
        website_id=website_id,
        url=url,
    )


def _count_nodes(tx, project_id: int, website_id: int) -> int:
    record = tx.run(
        """
        MATCH (n {project_id: $project_id, website_id: $website_id})
        RETURN count(n) AS c
        """,
        project_id=project_id,
        website_id=website_id,
    ).single()
    return int(record["c"]) if record is not None else 0
