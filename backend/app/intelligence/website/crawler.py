"""BFS crawler — checkpoint 3.B.4.

Start URL plus sitemap URLs, same-origin only. Limits are spec and
non-negotiable: max depth, max URLs, domain restriction, URL
normalisation, duplicate detection, robots, query-parameter filtering,
content-type filtering. Hitting a cap completes the run as `partial`
with the cap named — never as a clean full crawl.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from selectolax.parser import HTMLParser

from app.core.config import Settings, get_settings
from app.intelligence.website.fetch import (
    FetchError,
    FetchResult,
    LookupFn,
    fetch_public,
)
from app.intelligence.website.robots import (
    RobotsState,
    fetch_robots,
    is_allowed_by_robots,
)
from app.intelligence.website.sitemap import SitemapEvidence, discover_sitemaps
from app.intelligence.website.urls import (
    align_url_to_origin,
    is_crawlable_html,
    is_html_content_type,
    normalize_url,
    origin_of,
    same_origin,
    same_site,
    should_skip_path,
)

__all__ = [
    "CrawledPage",
    "SkippedUrl",
    "CrawlResult",
    "align_url_to_origin",
    "crawl",
    "extract_hrefs",
    "is_html_content_type",
    "normalize_url",
    "origin_of",
    "same_origin",
    "same_site",
    "should_skip_path",
]


@dataclass
class CrawledPage:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    redirect_chain: list[str]
    depth: int
    discovery_source: str
    in_sitemap: bool
    robots_allowed: bool
    truncated: bool
    headers: dict[str, str] = field(default_factory=dict)
    fetch_ms: int = 0


@dataclass
class SkippedUrl:
    url: str
    reason: str
    depth: int


@dataclass
class CrawlResult:
    status: str
    cap_reason: str | None
    pages: list[CrawledPage]
    skipped: list[SkippedUrl]
    failed_urls: list[str]
    robots: RobotsState
    sitemap: SitemapEvidence
    crawled_not_in_sitemap: list[str] = field(default_factory=list)


@dataclass
class _QueueItem:
    url: str
    depth: int
    discovery_source: str


def extract_hrefs(html: str) -> list[str]:
    """Discoverable same-document hrefs: `<a>` and `<area>`."""

    hrefs: list[str] = []
    try:
        tree = HTMLParser(html)
    except Exception:
        return hrefs
    for node in tree.css("a[href], area[href]"):
        value = (node.attributes.get("href") or "").strip()
        if value:
            hrefs.append(value)
    return hrefs


def crawl(
    start_url: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    lookup: LookupFn | None = None,
    respect_robots: bool = True,
) -> CrawlResult:
    settings = settings or get_settings()
    start = normalize_url(start_url)
    if start is None:
        raise FetchError("start URL is not a public HTTP(S) URL")
    origin = origin_of(start)
    max_urls = settings.crawl_max_urls
    max_depth = settings.crawl_max_depth

    owned = client is None
    http = client or httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=settings.crawl_fetch_timeout_seconds,
    )
    try:
        seed_fetches: dict[str, FetchResult] = {}
        start_fetch: FetchResult | None = None
        try:
            start_fetch = fetch_public(
                start, settings=settings, client=http, lookup=lookup
            )
        except FetchError:
            start_fetch = None
        if start_fetch is not None:
            _index_seed(seed_fetches, start, start_fetch)
            final = normalize_url(start_fetch.final_url) or start_fetch.final_url
            if same_site(final, origin):
                origin = origin_of(final)
                aligned_start = align_url_to_origin(start, origin)
                if aligned_start:
                    seed_fetches[aligned_start] = start_fetch
                aligned_final = align_url_to_origin(final, origin)
                if aligned_final:
                    seed_fetches[aligned_final] = start_fetch

        robots = fetch_robots(origin, settings=settings, client=http, lookup=lookup)
        sitemap = discover_sitemaps(
            origin,
            robots.sitemap_urls,
            settings=settings,
            client=http,
            lookup=lookup,
            sample=True,
        )
        return _bfs(
            start=start,
            origin=origin,
            robots=robots,
            sitemap=sitemap,
            max_urls=max_urls,
            max_depth=max_depth,
            respect_robots=respect_robots,
            settings=settings,
            http=http,
            lookup=lookup,
            seed_fetches=seed_fetches,
        )
    finally:
        if owned:
            http.close()


def _index_seed(seeds: dict[str, FetchResult], requested: str, result: FetchResult) -> None:
    for raw in (requested, result.url, result.final_url):
        key = normalize_url(raw) or raw
        seeds[key] = result
        aligned = align_url_to_origin(key, origin_of(result.final_url or requested))
        if aligned:
            seeds[aligned] = result


def _bfs(
    *,
    start: str,
    origin: str,
    robots: RobotsState,
    sitemap: SitemapEvidence,
    max_urls: int,
    max_depth: int,
    respect_robots: bool,
    settings: Settings,
    http: httpx.Client,
    lookup: LookupFn | None,
    seed_fetches: dict[str, FetchResult] | None = None,
) -> CrawlResult:
    seeds = seed_fetches or {}
    sitemap_set: set[str] = set()
    for raw in sitemap.page_urls:
        normalized = normalize_url(raw)
        if not normalized:
            continue
        sitemap_set.add(normalized)
        aligned = align_url_to_origin(normalized, origin)
        if aligned:
            sitemap_set.add(aligned)
    queue: deque[_QueueItem] = deque()
    seen_queue: set[str] = set()

    def enqueue(url: str, depth: int, source: str) -> None:
        normalized = align_url_to_origin(url, origin) or normalize_url(url)
        if normalized is None or normalized in seen_queue:
            return
        if not same_site(normalized, origin):
            return
        if should_skip_path(normalized):
            return
        seen_queue.add(normalized)
        queue.append(_QueueItem(url=normalized, depth=depth, discovery_source=source))

    enqueue(start, 0, "start")
    for sitemap_url in sitemap.page_urls:
        enqueue(sitemap_url, 0, "sitemap")

    visited: set[str] = set()
    pages: list[CrawledPage] = []
    skipped: list[SkippedUrl] = []
    failed_urls: list[str] = []
    url_capped = False
    depth_capped = False

    while queue:
        if len(pages) >= max_urls:
            url_capped = True
            break
        item = queue.popleft()
        if item.url in visited:
            continue
        if item.depth > max_depth:
            depth_capped = True
            skipped.append(SkippedUrl(url=item.url, reason="CRAWL_MAX_DEPTH", depth=item.depth))
            continue
        visited.add(item.url)

        pathname = urlsplit(item.url).path or "/"
        if respect_robots and not is_allowed_by_robots(pathname, robots.our_rules):
            skipped.append(
                SkippedUrl(url=item.url, reason="robots_disallow", depth=item.depth)
            )
            continue

        result = seeds.pop(item.url, None)
        if result is None:
            try:
                result = fetch_public(
                    item.url, settings=settings, client=http, lookup=lookup
                )
            except FetchError:
                failed_urls.append(item.url)
                continue

        final = align_url_to_origin(result.final_url, origin) or (
            normalize_url(result.final_url) or result.final_url
        )
        if final != item.url:
            visited.add(final)

        if not is_crawlable_html(result.content_type, result.body):
            skipped.append(
                SkippedUrl(url=item.url, reason="non_html", depth=item.depth)
            )
            continue

        page = CrawledPage(
            url=item.url,
            final_url=final,
            status_code=result.status_code,
            content_type=result.content_type,
            body=result.body,
            redirect_chain=list(result.redirect_chain),
            depth=item.depth,
            discovery_source=item.discovery_source,
            in_sitemap=item.url in sitemap_set or final in sitemap_set,
            robots_allowed=True,
            truncated=result.truncated,
            headers=dict(result.headers),
            fetch_ms=result.fetch_ms,
        )
        pages.append(page)

        for href in extract_hrefs(result.text):
            link = normalize_url(href, base=result.final_url or final)
            if link is None:
                continue
            next_depth = item.depth + 1
            if next_depth > max_depth:
                depth_capped = True
                continue
            enqueue(link, next_depth, "link")

    if url_capped or (len(pages) >= max_urls and queue):
        status = "partial"
        cap_reason = "CRAWL_MAX_URLS"
    elif depth_capped:
        status = "partial"
        cap_reason = "CRAWL_MAX_DEPTH"
    elif not pages and failed_urls:
        status = "failed"
        cap_reason = None
    else:
        status = "succeeded"
        cap_reason = None

    crawled_not_in_sitemap = [
        page.url for page in pages if not page.in_sitemap
    ]
    return CrawlResult(
        status=status,
        cap_reason=cap_reason,
        pages=pages,
        skipped=skipped,
        failed_urls=failed_urls,
        robots=robots,
        sitemap=sitemap,
        crawled_not_in_sitemap=crawled_not_in_sitemap,
    )
