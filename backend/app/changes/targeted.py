"""Targeted validation (step 7.6, `[SPEC AGENTS.md §36]`).

`ProductMetadata -> ProductPage -> ProductRoute -> /products/a, /products/b`
— walk the Neo4j code graph out from the changed files to any reachable
`Route` node (bounded hops, mirroring `app.agents.tools.get_graph_neighbours`),
then match each Route's Next.js-style path (`/products/[id]`) against
crawled `WebsitePage` URLs. Never falls back to "validate every crawled
page" — a route the graph or the crawl cannot resolve is an explicit gap,
not a silent full-site recrawl `[SPEC §39]`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.intelligence.repository.graph import GraphError, get_driver
from app.models.website import Website, WebsitePage

_MAX_HOPS = 3
_DYNAMIC_SEGMENT_RE = re.compile(r"\[\.\.\.[^\]]+\]|\[[^\]]+\]")


@dataclass
class TargetedValidationResult:
    urls: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


def _route_pattern(route_name: str) -> re.Pattern[str]:
    """Turn a Next.js app-router path into a matching regex.

    `[id]` -> one path segment; `[...slug]` -> the remainder of the path.
    """

    parts = route_name.strip("/").split("/") if route_name.strip("/") else []
    regex_parts: list[str] = []
    for part in parts:
        if part.startswith("[...") and part.endswith("]"):
            regex_parts.append(".*")
        elif part.startswith("[") and part.endswith("]"):
            regex_parts.append("[^/]+")
        else:
            regex_parts.append(re.escape(part))
    pattern = "^/" + "/".join(regex_parts) + "/?$"
    return re.compile(pattern)


def _affected_routes(
    project_id: int, repository_id: int, changed_files: list[str]
) -> tuple[list[str], str | None]:
    try:
        driver = get_driver()
    except GraphError as exc:
        return [], f"code graph unavailable: {exc}"
    try:
        with driver.session() as session:
            records = session.run(
                f"""
                MATCH (start {{project_id: $project_id, repository_id: $repository_id}})
                WHERE start.file_path IN $files
                MATCH (start)-[:ROUTES_TO|CALLS|RENDERS|IMPORTS*0..{_MAX_HOPS}]-(r:Route
                    {{project_id: $project_id, repository_id: $repository_id}})
                RETURN DISTINCT r.name AS route
                """,
                project_id=project_id,
                repository_id=repository_id,
                files=changed_files,
            )
            routes = sorted({record["route"] for record in records if record["route"]})
    except GraphError as exc:
        return [], f"code graph unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 - a graph read failure is a gap, not a crash
        return [], f"code graph query failed: {exc}"
    return routes, None


def resolve_affected_urls(
    db: Session,
    *,
    project_id: int,
    repository_id: int,
    website_id: int | None,
    changed_files: list[str],
    settings: Settings | None = None,
    fallback_url: str | None = None,
) -> TargetedValidationResult:
    """Walk the code graph for reachable Route(s); if that comes up empty
    (`gap`), fall back to the Finding's own `affected_url` — the exact page
    the finding was raised against, already known before this patch ever
    ran. That is still one specific page, never "validate every crawled
    page" (`[SPEC §39]`); it just uses a source of truth the graph walk
    cannot see, e.g. a static HTML entry point tree-sitter never links to a
    `Route` node, or a router library other than Next.js app-router.
    """

    settings = settings or get_settings()
    result = TargetedValidationResult()

    routes, gap = _affected_routes(project_id, repository_id, changed_files)
    if gap:
        result.gaps.append(gap)
    elif not routes:
        result.gaps.append("no Route node reachable from the changed files")
    else:
        result.routes = routes
        if website_id is None:
            result.gaps.append("no website attached; route(s) resolved but not matched to a URL")
        else:
            website = db.get(Website, website_id)
            pages = list(
                db.scalars(select(WebsitePage).where(WebsitePage.website_id == website_id))
            )
            if not pages:
                result.gaps.append("website has no crawled pages yet; browser validation skipped")
            else:
                patterns = [(route, _route_pattern(route)) for route in routes]
                matched: list[str] = []
                unmatched_routes: set[str] = set(routes)
                base = website.url if website is not None else ""
                for page in pages:
                    path = _url_path(page.url, base)
                    for route, pattern in patterns:
                        if pattern.match(path):
                            matched.append(page.url)
                            unmatched_routes.discard(route)
                            break
                    if len(matched) >= settings.targeted_validation_max_urls:
                        break

                if unmatched_routes:
                    result.gaps.append(
                        "no crawled URL matched route(s): " + ", ".join(sorted(unmatched_routes))
                    )
                result.urls = matched[: settings.targeted_validation_max_urls]

    if not result.urls and fallback_url:
        result.gaps.append(
            f"code graph/crawl could not resolve a URL for the changed files; "
            f"used the finding's own affected URL as a fallback: {fallback_url}"
        )
        result.urls = [fallback_url]

    return result


def _url_path(url: str, base: str) -> str:
    remainder = url
    if base and url.startswith(base):
        remainder = url[len(base) :]
    if "://" in remainder:
        remainder = remainder.split("://", 1)[1]
        remainder = "/" + remainder.split("/", 1)[1] if "/" in remainder else "/"
    if not remainder.startswith("/"):
        remainder = "/" + remainder
    return remainder.split("?", 1)[0].split("#", 1)[0]
