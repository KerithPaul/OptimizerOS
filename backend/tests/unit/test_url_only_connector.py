"""UrlOnlyConnector fetch is real; mutations raise (step 3.D.2 verify)."""

import httpx
import pytest

from app.connectors.base import CapabilityNotSupported
from app.connectors.capabilities import ModificationLevel
from app.connectors.url_only import UrlOnlyConnector
from app.core.config import get_settings


def _lookup(host: str) -> list[str]:
    if host == "example.com":
        return ["93.184.216.34"]
    raise OSError("unrecognised test host")


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=body)


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )


def _connector(handler) -> UrlOnlyConnector:
    return UrlOnlyConnector(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 10, "crawl_max_depth": 2}),
        client=_client(handler),
        lookup=_lookup,
    )


def _site_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    if path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
        return httpx.Response(404)
    if path == "/":
        return _html("<html><head><title>Home</title></head><body><h1>Home</h1></body></html>")
    return httpx.Response(404)


def test_update_metadata_raises_capability_not_supported() -> None:
    connector = _connector(_site_handler)
    with pytest.raises(CapabilityNotSupported) as exc:
        connector.update_metadata("http://example.com/", {"title": "x"})
    assert exc.value.method == "update_metadata"
    assert exc.value.platform == "url_only"
    assert "does not support update_metadata" in str(exc.value)


def test_snapshot_and_rollback_raise() -> None:
    connector = _connector(_site_handler)
    with pytest.raises(CapabilityNotSupported) as exc:
        connector.create_snapshot()
    assert exc.value.method == "create_snapshot"
    with pytest.raises(CapabilityNotSupported) as exc:
        connector.rollback("snap-1")
    assert exc.value.method == "rollback"


def test_capability_report_says_modification_snapshot_rollback_are_no() -> None:
    report = _connector(_site_handler).get_capabilities()
    assert report.allows_modification() is False
    assert report.snapshot is False
    assert report.automatic_rollback is ModificationLevel.NONE
    assert report.seo_modification is ModificationLevel.NONE


def test_discover_crawls_and_extracts() -> None:
    connector = _connector(_site_handler)
    pages = connector.discover()
    assert pages
    assert any(page.title == "Home" for page in pages)
    assert connector.last_crawl is not None
    assert connector.fetch_site_metadata().platform == "url_only"
    schema = connector.fetch_schema(pages[0].url)
    assert schema == pages[0].structured_data


def test_authenticate_is_a_noop() -> None:
    _connector(_site_handler).authenticate()
