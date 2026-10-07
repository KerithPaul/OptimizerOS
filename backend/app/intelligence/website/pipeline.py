"""URL-only crawl → extract → bounded render → Lighthouse (step 3.D).

The connector owns fetch/discover. This pipeline adds the website-
intelligence steps that are not connector methods, then the job persists.
Render and Lighthouse failures are recorded states and do not abort.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import httpx

from app.connectors.model import Page
from app.connectors.url_only import UrlOnlyConnector
from app.core.config import Settings, get_settings
from app.intelligence.website.crawler import CrawlResult, origin_of
from app.intelligence.website.extract import inspect_llms_txt
from app.intelligence.website.fetch import LookupFn
from app.intelligence.website.lighthouse import (
    LighthouseRunner,
    lab_signal_to_record,
    run_lighthouse,
)
from app.intelligence.website.render import Renderer, apply_render


@dataclass
class AnalyzedSite:
    start_url: str
    crawl: CrawlResult
    pages: list[Page]
    observations: dict[str, dict] = field(default_factory=dict)
    renders: list[dict] = field(default_factory=list)
    lighthouse: list[dict] = field(default_factory=list)
    llms_txt: dict | None = None


def analyze_site(
    start_url: str,
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    lookup: LookupFn | None = None,
    connector: UrlOnlyConnector | None = None,
    renderer: Renderer | None = None,
    lighthouse_runner: LighthouseRunner | None = None,
    skip_render: bool = False,
    skip_lighthouse: bool = False,
) -> AnalyzedSite:
    settings = settings or get_settings()
    connector = connector or UrlOnlyConnector(
        start_url, settings=settings, client=client, lookup=lookup
    )
    pages = connector.discover()
    crawl = connector.last_crawl
    if crawl is None:
        raise RuntimeError("UrlOnlyConnector.discover did not record a crawl")

    observations = {
        item.page.url: asdict(item.observations) for item in connector.last_extractions
    }

    llms = None
    try:
        origin = origin_of(normalize_or_start(start_url))
        observed = inspect_llms_txt(
            origin, settings=settings, client=client, lookup=lookup
        )
        llms = {
            "url": observed.url,
            "state": observed.state,
            "http_status": observed.http_status,
        }
    except Exception:
        llms = {"url": start_url, "state": "unavailable", "http_status": 0}

    renders = _render_sample(
        connector,
        pages,
        settings=settings,
        renderer=renderer,
        skip_render=skip_render,
        lookup=lookup,
    )

    lighthouse = []
    if not skip_lighthouse:
        sample = pages[: max(0, settings.crawl_lighthouse_max_pages)]
        if not sample:
            sample_url = start_url
            signal = run_lighthouse(
                sample_url,
                settings=settings,
                lookup=lookup,
                runner=lighthouse_runner,
            )
            lighthouse.append(lab_signal_to_record(signal))
        else:
            for page in sample:
                signal = run_lighthouse(
                    page.url,
                    settings=settings,
                    lookup=lookup,
                    runner=lighthouse_runner,
                )
                lighthouse.append(lab_signal_to_record(signal))

    return AnalyzedSite(
        start_url=start_url,
        crawl=crawl,
        pages=pages,
        observations=observations,
        renders=renders,
        lighthouse=lighthouse,
        llms_txt=llms,
    )


def normalize_or_start(start_url: str) -> str:
    from app.intelligence.website.crawler import normalize_url

    return normalize_url(start_url) or start_url


def _render_sample(
    connector: UrlOnlyConnector,
    pages: list[Page],
    *,
    settings: Settings,
    renderer: Renderer | None,
    skip_render: bool,
    lookup: LookupFn | None,
) -> list[dict]:
    if skip_render or settings.crawl_render_max_pages <= 0:
        return []
    crawl = connector.last_crawl
    if crawl is None:
        return []
    html_by_url = {page.url: page.body for page in crawl.pages}
    limit = min(len(pages), settings.crawl_render_max_pages)
    owned = renderer is None
    active = renderer or Renderer(settings=settings, lookup=lookup)
    records: list[dict] = []
    try:
        for index, page in enumerate(pages[:limit]):
            raw = html_by_url.get(page.url)
            if raw is None:
                result = active.render_url(page.url, raw_html_hash=page.raw_html_hash)
            else:
                html = raw.decode("utf-8", errors="replace")
                result = active.render_html(
                    html, url=page.url, raw_html_hash=page.raw_html_hash
                )
            pages[index] = apply_render(page, result)
            records.append(
                {
                    "url": result.url,
                    "state": result.state,
                    "raw_to_rendered_changed": result.raw_to_rendered_changed,
                    "message": result.message,
                    "console_error_count": len(result.console_errors),
                    "request_failure_count": len(result.request_failures),
                }
            )
    finally:
        if owned:
            active.close()
    return records
