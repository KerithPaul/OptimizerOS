"""BFS crawler limits, robots compliance, and partial cap (step 3.B.4)."""

import httpx

from app.core.config import get_settings
from app.intelligence.website.crawler import crawl, normalize_url


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


def test_normalize_strips_fragment_tracking_params_and_trailing_slash() -> None:
    assert (
        normalize_url("http://Example.com/a/?utm_source=x#frag")
        == "http://example.com/a"
    )
    assert (
        normalize_url("http://example.com/publications?search=Bail")
        == "http://example.com/publications"
    )


def test_disallowed_path_is_not_fetched() -> None:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /secret\n",
            )
        if request.url.path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if request.url.path == "/":
            return _html('<a href="/ok">ok</a><a href="/secret">no</a>')
        if request.url.path == "/ok":
            return _html("<p>ok</p>")
        if request.url.path == "/secret":
            return _html("<p>should not be fetched</p>")
        return httpx.Response(404)

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 20, "crawl_max_depth": 3}),
        client=_client(handler),
        lookup=_lookup,
    )
    assert "/secret" not in fetched
    assert all(page.url.endswith("/secret") is False for page in result.pages)
    assert any(skip.reason == "robots_disallow" for skip in result.skipped)


def test_unavailable_robots_does_not_crash_the_crawl() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("robots down")
        if request.url.path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        return _html("<html><p>home</p></html>")

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 5, "crawl_max_depth": 1}),
        client=_client(handler),
        lookup=_lookup,
    )
    assert result.robots.state == "unavailable"
    assert result.status in {"succeeded", "partial"}
    assert any(page.url.rstrip("/") == "http://example.com" for page in result.pages)


def test_site_larger_than_max_urls_is_partial_with_cap_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html(
                "".join(f'<a href="/p{i}">p{i}</a>' for i in range(5))
            )
        return _html(f"<p>{path}</p>")

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 2, "crawl_max_depth": 4}),
        client=_client(handler),
        lookup=_lookup,
    )
    assert result.status == "partial"
    assert result.cap_reason == "CRAWL_MAX_URLS"
    assert len(result.pages) == 2


def test_depth_cap_is_partial_with_cap_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html('<a href="/level1">one</a>')
        if path == "/level1":
            return _html('<a href="/level2">two</a>')
        return _html("<p>too deep</p>")

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 20, "crawl_max_depth": 0}),
        client=_client(handler),
        lookup=_lookup,
    )
    assert result.status == "partial"
    assert result.cap_reason == "CRAWL_MAX_DEPTH"
    assert all(page.depth == 0 for page in result.pages)
    assert not any(page.url.endswith("/level1") for page in result.pages)


def test_http_start_crawls_https_same_host_sitemap_and_links() -> None:
    """HTTP→HTTPS on the same host is in-domain, not a different origin."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path == "/sitemap.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/from-sitemap</loc></url>
            </urlset>
            """
            return httpx.Response(
                200, headers={"content-type": "application/xml"}, text=body
            )
        if path in {"/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html('<a href="https://example.com/from-link">x</a>')
        return _html(f"<p>{path}</p>")

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(
            update={"crawl_max_urls": 20, "crawl_max_depth": 2}
        ),
        client=_client(handler),
        lookup=_lookup,
    )
    urls = {page.url for page in result.pages}
    assert any(url.endswith("/from-sitemap") for url in urls)
    assert any(url.endswith("/from-link") for url in urls)


def test_external_and_non_html_are_not_crawled() -> None:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html(
                '<a href="https://other.example/x">out</a><a href="/file.pdf">pdf</a>'
            )
        if path == "/file.pdf":
            return httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=b"%PDF"
            )
        return httpx.Response(404)

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 10, "crawl_max_depth": 2}),
        client=_client(handler),
        lookup=_lookup,
    )
    assert not any("other.example" in url for url in fetched)
    assert any(skip.reason == "non_html" for skip in result.skipped)
    assert all(page.url.rstrip("/").endswith("example.com") for page in result.pages)


def test_sitemap_urls_are_seeded_and_crawled_not_in_sitemap_is_recorded() -> None:
    sitemap = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://example.com/from-sitemap</loc></url>
</urlset>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nSitemap: http://example.com/sitemap.xml\n")
        if path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        if path in {"/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html('<a href="/orphan">orphan</a>')
        return _html("<p>ok</p>")

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(update={"crawl_max_urls": 20, "crawl_max_depth": 2}),
        client=_client(handler),
        lookup=_lookup,
    )
    urls = {page.url for page in result.pages}
    assert any(url.endswith("/from-sitemap") for url in urls)
    assert any(url.endswith("/orphan") for url in urls)
    assert any(page.url.endswith("/orphan") and not page.in_sitemap for page in result.pages)


def test_www_start_crawls_apex_sitemap_and_links() -> None:
    """www vs apex is the same site — the drmoksha.com failure mode."""

    def lookup(host: str) -> list[str]:
        if host in {"example.com", "www.example.com"}:
            return ["93.184.216.34"]
        raise OSError("unrecognised test host")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "www.example.com":
            return httpx.Response(
                301, headers={"location": f"https://example.com{path}"}
            )
        if path == "/robots.txt":
            return httpx.Response(
                200,
                text=(
                    "User-agent: *\nAllow: /\n"
                    "Sitemap: https://example.com/sitemap.xml\n"
                ),
            )
        if path == "/sitemap.xml":
            body = """<?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/from-sitemap</loc></url>
            </urlset>
            """
            return httpx.Response(
                200, headers={"content-type": "application/xml"}, text=body
            )
        if path in {"/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html('<a href="https://example.com/from-link">x</a>')
        return _html(f"<p>{path}</p>")

    result = crawl(
        "https://www.example.com/",
        settings=get_settings().model_copy(
            update={"crawl_max_urls": 20, "crawl_max_depth": 2}
        ),
        client=_client(handler),
        lookup=lookup,
    )
    urls = {page.url for page in result.pages}
    assert any(url.rstrip("/").endswith("example.com") for url in urls)
    assert any(url.endswith("/from-sitemap") for url in urls)
    assert any(url.endswith("/from-link") for url in urls)
    assert all("www.example.com" not in url for url in urls)
    assert all(page.fetch_ms >= 0 for page in result.pages)
    assert len(result.pages) >= 3


def test_empty_content_type_is_crawled_when_body_looks_like_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(
                200,
                headers={"content-type": ""},
                text='<html><a href="/next">next</a></html>',
            )
        if path == "/next":
            return _html("<p>next</p>")
        return httpx.Response(404)

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(
            update={"crawl_max_urls": 10, "crawl_max_depth": 2}
        ),
        client=_client(handler),
        lookup=_lookup,
    )
    urls = {page.url for page in result.pages}
    assert any(url.rstrip("/").endswith("example.com") for url in urls)
    assert any(url.endswith("/next") for url in urls)


def test_search_query_and_cdn_cgi_are_not_crawled_as_pages() -> None:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path in {"/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"}:
            return httpx.Response(404)
        if path == "/":
            return _html(
                '<a href="/publications?search=Bail">tag</a>'
                '<a href="/cdn-cgi/l/email-protection">email</a>'
                '<a href="/about">about</a>'
            )
        return _html(f"<p>{path}</p>")

    result = crawl(
        "http://example.com/",
        settings=get_settings().model_copy(
            update={"crawl_max_urls": 10, "crawl_max_depth": 2}
        ),
        client=_client(handler),
        lookup=_lookup,
    )
    urls = {page.url for page in result.pages}
    assert any(url.endswith("/about") for url in urls)
    assert not any("search=" in url for url in urls)
    assert not any("cdn-cgi" in url for url in urls)
    assert not any("cdn-cgi" in url for url in fetched)
