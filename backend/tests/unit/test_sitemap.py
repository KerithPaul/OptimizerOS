"""Sitemap index vs urlset, including nested indexes (step 3.B.3 verify)."""

import httpx

from app.core.config import get_settings
from app.intelligence.website.sitemap import discover_sitemaps, parse_sitemap_xml

_ORIGIN = "http://example.com"

_INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>http://example.com/sitemap-a.xml</loc>
  </sitemap>
  <sitemap>
    <loc>http://example.com/sitemap-b.xml</loc>
  </sitemap>
</sitemapindex>
"""

_CHILD_A = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>http://example.com/a</loc>
    <lastmod>2026-01-01</lastmod>
  </url>
  <url>
    <loc>http://example.com/a</loc>
  </url>
</urlset>
"""

_CHILD_B = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>http://example.com/b</loc>
    <lastmod>not-a-date</lastmod>
  </url>
</urlset>
"""


def _lookup(host: str) -> list[str]:
    if host == "example.com":
        return ["93.184.216.34"]
    raise OSError("unrecognised test host")


def test_parse_urlset_records_duplicates_and_lastmod() -> None:
    record = parse_sitemap_xml(_CHILD_A, _ORIGIN, "http://example.com/sitemap-a.xml", 200)
    assert record.parse_state == "valid"
    assert record.kind == "urlset"
    assert record.page_urls == ["http://example.com/a"]
    assert record.duplicate_loc_count == 1
    assert record.lastmod_present_count == 1
    assert record.invalid_lastmod_count == 0


def test_invalid_xml_is_recorded_not_raised() -> None:
    record = parse_sitemap_xml("<not-xml", _ORIGIN, "http://example.com/sitemap.xml", 200)
    assert record.parse_state == "invalid"
    assert record.page_urls == []


def test_nested_sitemap_index_resolves_to_leaf_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sitemap_index.xml":
            return httpx.Response(200, text=_INDEX)
        if path == "/sitemap-a.xml":
            return httpx.Response(200, text=_CHILD_A)
        if path == "/sitemap-b.xml":
            return httpx.Response(200, text=_CHILD_B)
        if path in {"/a", "/b"}:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    evidence = discover_sitemaps(
        _ORIGIN,
        ["http://example.com/sitemap_index.xml"],
        settings=get_settings(),
        client=client,
        lookup=_lookup,
        sample=True,
    )
    assert "http://example.com/a" in evidence.page_urls
    assert "http://example.com/b" in evidence.page_urls
    kinds = {record.url: record.kind for record in evidence.records if record.parse_state == "valid"}
    assert kinds["http://example.com/sitemap_index.xml"] == "index"
    child_a = next(r for r in evidence.records if r.url.endswith("sitemap-a.xml"))
    assert child_a.duplicate_loc_count == 1
    child_b = next(r for r in evidence.records if r.url.endswith("sitemap-b.xml"))
    assert child_b.invalid_lastmod_count == 1
    sample_urls = {row.url for row in evidence.sampled_outcomes}
    assert "http://example.com/a" in sample_urls
    assert "http://example.com/b" in sample_urls


def test_urlset_keeps_www_equivalent_locs() -> None:
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/from-apex</loc></url>
</urlset>
"""
    record = parse_sitemap_xml(
        xml, "https://www.example.com", "https://www.example.com/sitemap.xml", 200
    )
    assert record.parse_state == "valid"
    assert record.page_urls == ["https://example.com/from-apex"]


def test_gzip_sitemap_is_decompressed() -> None:
    import gzip

    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://example.com/gzipped</loc></url>
</urlset>
"""
    compressed = gzip.compress(xml.encode("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sitemap.xml.gz":
            return httpx.Response(
                200,
                headers={"content-type": "application/gzip"},
                content=compressed,
            )
        if path in {"/sitemap.xml", "/sitemap_index.xml", "/gzipped"}:
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html></html>"
            )
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    evidence = discover_sitemaps(
        _ORIGIN,
        ["http://example.com/sitemap.xml.gz"],
        settings=get_settings(),
        client=client,
        lookup=_lookup,
        sample=False,
    )
    assert "http://example.com/gzipped" in evidence.page_urls


def test_undeclared_html_conventional_sitemap_is_unavailable() -> None:
    """SPA catch-all: /sitemap_index.xml returns index.html. That is not a
    malformed sitemap when robots never declared it."""

    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://example.com/</loc></url>
</urlset>
"""
    html = "<!doctype html><html lang='en'><title>home</title></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sitemap.xml":
            return httpx.Response(
                200, headers={"content-type": "application/xml"}, text=xml
            )
        if path in {"/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=UTF-8"}, text=html
            )
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    evidence = discover_sitemaps(
        _ORIGIN,
        ["http://example.com/sitemap.xml"],
        settings=get_settings(),
        client=client,
        lookup=_lookup,
        sample=False,
    )
    by_url = {record.url: record for record in evidence.records}
    assert by_url["http://example.com/sitemap.xml"].parse_state == "valid"
    assert by_url["http://example.com/sitemap_index.xml"].parse_state == "unavailable"
    assert "http://example.com/" in evidence.page_urls


def test_declared_html_sitemap_is_still_invalid() -> None:
    html = "<!doctype html><html lang='en'><title>home</title></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=UTF-8"}, text=html
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    evidence = discover_sitemaps(
        _ORIGIN,
        ["http://example.com/sitemap.xml"],
        settings=get_settings(),
        client=client,
        lookup=_lookup,
        sample=False,
    )
    declared = next(r for r in evidence.records if r.url.endswith("/sitemap.xml"))
    assert declared.parse_state == "invalid"
