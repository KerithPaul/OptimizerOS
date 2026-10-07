"""UrlOnlyConnector — checkpoint 3.D.2.

Fetch, discover, and crawl are real. Every mutation, snapshot, and
rollback method raises `CapabilityNotSupported`. The capability report
states modification = no, snapshot = no, rollback = no.
"""

from __future__ import annotations

import httpx

from app.connectors.base import CapabilityNotSupported, WebsiteConnector
from app.connectors.capabilities import (
    URL_ONLY_PLATFORM,
    CapabilityReport,
    url_only_capabilities,
)
from app.connectors.model import Image, Page, PlatformMetadata, StructuredData
from app.core.config import Settings, get_settings
from app.intelligence.website.crawler import CrawlResult, crawl, normalize_url
from app.intelligence.website.extract import ExtractionResult, extract_crawled_page, extract_page
from app.intelligence.website.fetch import LookupFn, fetch_public


class UrlOnlyConnector(WebsiteConnector):
    """URL-only access: public fetch only. Cannot modify the site."""

    def __init__(
        self,
        start_url: str,
        *,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        lookup: LookupFn | None = None,
    ) -> None:
        self.start_url = start_url
        self.settings = settings or get_settings()
        self.client = client
        self.lookup = lookup
        self._loaded = False
        self._crawl: CrawlResult | None = None
        self._extractions: list[ExtractionResult] = []
        self._pages: list[Page] = []
        self._by_url: dict[str, Page] = {}

    @property
    def last_crawl(self) -> CrawlResult | None:
        return self._crawl

    @property
    def last_extractions(self) -> list[ExtractionResult]:
        return list(self._extractions)

    def discover(self) -> list[Page]:
        self._load()
        return list(self._pages)

    def authenticate(self) -> None:
        return None

    def fetch_site_metadata(self) -> PlatformMetadata:
        return PlatformMetadata(
            platform=URL_ONLY_PLATFORM,
            home_url=self.start_url,
        )

    def fetch_pages(self) -> list[Page]:
        return self.discover()

    def fetch_content(self, url: str) -> Page:
        return self._page(url)

    def fetch_metadata(self, url: str) -> Page:
        return self._page(url)

    def fetch_schema(self, url: str) -> list[StructuredData]:
        return list(self._page(url).structured_data)

    def fetch_media(self, url: str) -> list[Image]:
        return list(self._page(url).images)

    def update_content(self, url: str, content: str) -> None:
        raise self._unsupported("update_content")

    def update_metadata(self, url: str, metadata: dict) -> None:
        raise self._unsupported("update_metadata")

    def update_schema(self, url: str, schema: list[StructuredData]) -> None:
        raise self._unsupported("update_schema")

    def create_snapshot(self) -> str:
        raise self._unsupported("create_snapshot")

    def rollback(self, snapshot_id: str) -> None:
        raise self._unsupported("rollback")

    def get_capabilities(self) -> CapabilityReport:
        return url_only_capabilities()

    def _unsupported(self, method: str) -> CapabilityNotSupported:
        return CapabilityNotSupported(
            method,
            platform=URL_ONLY_PLATFORM,
            reason=(
                f"{URL_ONLY_PLATFORM} does not support {method}; "
                "modification, snapshot, and rollback are not possible"
            ),
        )

    def _load(self) -> None:
        if self._loaded:
            return
        self._crawl = crawl(
            self.start_url,
            settings=self.settings,
            client=self.client,
            lookup=self.lookup,
        )
        self._extractions = [extract_crawled_page(page) for page in self._crawl.pages]
        self._pages = [item.page for item in self._extractions]
        self._by_url = {}
        for crawled, page in zip(self._crawl.pages, self._pages, strict=True):
            self._by_url[page.url] = page
            self._by_url.setdefault(crawled.url, page)
            self._by_url.setdefault(crawled.final_url, page)
        self._loaded = True

    def _page(self, url: str) -> Page:
        self._load()
        key = normalize_url(url) or url
        found = self._by_url.get(key) or self._by_url.get(url)
        if found is not None:
            return found
        result = fetch_public(
            url, settings=self.settings, client=self.client, lookup=self.lookup
        )
        extracted = extract_page(
            result.text,
            url=normalize_url(result.final_url) or result.final_url,
            status_code=result.status_code,
            redirect_chain=list(result.redirect_chain),
        )
        self._by_url[extracted.page.url] = extracted.page
        return extracted.page
