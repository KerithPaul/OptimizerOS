"""Sitemap discovery — checkpoint 3.B.3.

Discover from robots.txt declarations and conventional locations
(`/sitemap.xml`, `/sitemap_index.xml`). Handle sitemap index vs urlset.
Nested indexes resolve to leaf URLs. Parse failures are recorded, not a
crash. lastmod presence, duplicates, and sampled URL outcomes are kept
as later rule inputs.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

from app.core.config import Settings, get_settings
from app.intelligence.website.fetch import (
    FetchError,
    FetchResult,
    LookupFn,
    fetch_public,
    parse_public_url,
)
from app.intelligence.website.urls import same_site

_MAX_SITEMAP_FILES = 8
_MAX_LEAF_URLS = 500
_SAMPLE_LIMIT = 10
_LASTMOD_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$"
)


@dataclass
class SitemapRecord:
    url: str
    status: int
    parse_state: str
    kind: str
    page_urls: list[str] = field(default_factory=list)
    child_sitemaps: list[str] = field(default_factory=list)
    duplicate_loc_count: int = 0
    invalid_lastmod_count: int = 0
    lastmod_present_count: int = 0


@dataclass
class SitemapUrlOutcome:
    url: str
    status: int
    final_url: str | None
    outcome: str


@dataclass
class SitemapEvidence:
    page_urls: list[str]
    records: list[SitemapRecord]
    sampled_outcomes: list[SitemapUrlOutcome]


def parse_sitemap_xml(xml: str, origin: str, url: str, status: int) -> SitemapRecord:
    base = SitemapRecord(
        url=url, status=status, parse_state="unavailable", kind="unknown"
    )
    if not xml.strip():
        return base
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return SitemapRecord(
            url=url, status=status, parse_state="invalid", kind="unknown"
        )
    kind = _local(root.tag).lower()
    if kind not in ("urlset", "sitemapindex"):
        return SitemapRecord(
            url=url, status=status, parse_state="not_xml", kind="unknown"
        )

    locs: list[str] = []
    lastmods: list[str] = []
    for node in root:
        local = _local(node.tag).lower()
        expected = "url" if kind == "urlset" else "sitemap"
        if local != expected:
            continue
        loc_text = None
        lastmod_text = None
        for child in node:
            child_local = _local(child.tag).lower()
            if child_local == "loc" and child.text:
                loc_text = child.text.strip()
            elif child_local == "lastmod" and child.text:
                lastmod_text = child.text.strip()
        if loc_text:
            normalized = _same_origin_url(loc_text, origin)
            if normalized:
                locs.append(normalized)
        if lastmod_text:
            lastmods.append(lastmod_text)

    unique = list(dict.fromkeys(locs))
    invalid_lastmod = sum(1 for value in lastmods if not _valid_lastmod(value))
    return SitemapRecord(
        url=url,
        status=status,
        parse_state="valid",
        kind="urlset" if kind == "urlset" else "index",
        page_urls=unique[:_MAX_LEAF_URLS] if kind == "urlset" else [],
        child_sitemaps=unique[:20] if kind == "sitemapindex" else [],
        duplicate_loc_count=max(0, len(locs) - len(unique)),
        invalid_lastmod_count=invalid_lastmod,
        lastmod_present_count=len(lastmods),
    )


def discover_sitemaps(
    origin: str,
    declared: list[str] | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    lookup: LookupFn | None = None,
    sample: bool = True,
) -> SitemapEvidence:
    settings = settings or get_settings()
    origin = origin.rstrip("/")
    declared_urls = [raw for raw in (declared or []) if raw]
    declared_set = set(declared_urls)
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in [
        *declared_urls,
        origin + "/sitemap.xml",
        origin + "/sitemap_index.xml",
        origin + "/sitemap.xml.gz",
    ]:
        if raw and raw not in seen:
            seen.add(raw)
            candidates.append(raw)

    records: list[SitemapRecord] = []
    queue = candidates[:5]
    queued = set(queue)
    while queue and len(records) < _MAX_SITEMAP_FILES:
        candidate = queue.pop(0)
        try:
            result = fetch_public(
                candidate, settings=settings, client=client, lookup=lookup
            )
        except FetchError:
            records.append(
                SitemapRecord(
                    url=candidate,
                    status=0,
                    parse_state="unavailable",
                    kind="unknown",
                )
            )
            continue
        if result.status_code < 200 or result.status_code >= 300:
            records.append(
                SitemapRecord(
                    url=candidate,
                    status=result.status_code,
                    parse_state="unavailable",
                    kind="unknown",
                )
            )
            continue
        xml = _sitemap_text(result)
        # SPA catch-alls return 200 HTML for every path. A conventional
        # probe (sitemap_index.xml) that was not declared in robots.txt
        # is not a malformed sitemap — it is simply not a sitemap.
        # A robots-declared URL that serves HTML still parses as invalid.
        if candidate not in declared_set and _is_html_sitemap_response(result, xml):
            records.append(
                SitemapRecord(
                    url=candidate,
                    status=result.status_code,
                    parse_state="unavailable",
                    kind="unknown",
                )
            )
            continue
        record = parse_sitemap_xml(xml, origin, candidate, result.status_code)
        records.append(record)
        for child in record.child_sitemaps:
            if child not in queued and len(queued) < _MAX_SITEMAP_FILES:
                queued.add(child)
                queue.append(child)

    page_urls = list(
        dict.fromkeys(url for record in records for url in record.page_urls)
    )[:_MAX_LEAF_URLS]
    sampled: list[SitemapUrlOutcome] = []
    if sample:
        sampled = _sample_urls(
            page_urls[:_SAMPLE_LIMIT],
            origin,
            settings=settings,
            client=client,
            lookup=lookup,
        )
    return SitemapEvidence(
        page_urls=page_urls, records=records[:_MAX_SITEMAP_FILES], sampled_outcomes=sampled
    )


def _sample_urls(
    urls: list[str],
    origin: str,
    *,
    settings: Settings,
    client: httpx.Client | None,
    lookup: LookupFn | None,
) -> list[SitemapUrlOutcome]:
    outcomes: list[SitemapUrlOutcome] = []
    for url in urls:
        try:
            result = fetch_public(
                url,
                method="HEAD",
                settings=settings,
                client=client,
                lookup=lookup,
                timeout=min(8.0, settings.crawl_fetch_timeout_seconds),
            )
            if result.status_code in (405, 501):
                result = fetch_public(
                    url,
                    method="GET",
                    settings=settings,
                    client=client,
                    lookup=lookup,
                    timeout=min(8.0, settings.crawl_fetch_timeout_seconds),
                )
            final = _same_origin_url(result.final_url, origin)
            if 200 <= result.status_code < 300:
                outcome = "redirected" if final and final != url else "ok"
            else:
                outcome = "non_200"
            outcomes.append(
                SitemapUrlOutcome(
                    url=url,
                    status=result.status_code,
                    final_url=final,
                    outcome=outcome,
                )
            )
        except FetchError:
            outcomes.append(
                SitemapUrlOutcome(
                    url=url, status=0, final_url=None, outcome="unavailable"
                )
            )
    return outcomes


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _valid_lastmod(value: str) -> bool:
    return _LASTMOD_RE.match(value) is not None


def _is_html_sitemap_response(result: FetchResult, text: str) -> bool:
    stripped = text.lstrip()
    lower = stripped.lower()
    if lower.startswith("<?xml") or lower.startswith("<urlset") or lower.startswith(
        "<sitemapindex"
    ):
        return False
    content_type = (result.content_type or "").lower()
    if "html" in content_type:
        return True
    return lower.startswith("<!doctype html") or lower.startswith("<html")


def _sitemap_text(result: FetchResult) -> str:
    body = result.body
    if body.startswith(b"\x1f\x8b"):
        try:
            body = gzip.decompress(body)
        except OSError:
            return result.text[:2_000_000]
    return body.decode("utf-8", errors="replace")[:2_000_000]


def _same_origin_url(value: str, origin: str) -> str | None:
    try:
        joined = urljoin(origin + "/", value)
        parse_public_url(joined)
    except (FetchError, ValueError):
        return None
    loc = joined.split("#", 1)[0]
    if not same_site(loc, origin):
        return None
    return loc
