"""Crawl pipeline stores labelled Lighthouse signals (step 3.D / 3.C.3)."""

from pathlib import Path

import httpx

from app.core.config import get_settings
from app.intelligence.website.lighthouse import PROVENANCE, PROVENANCE_LABEL
from app.intelligence.website.pipeline import analyze_site

_GOLDEN_E = Path(__file__).resolve().parents[3] / "testdata" / "golden-projects" / "e-static-html"


def _lookup(host: str) -> list[str]:
    if host == "example.com":
        return ["93.184.216.34"]
    raise OSError("unrecognised test host")


def _html_file(name: str) -> httpx.Response:
    text = (_GOLDEN_E / name).read_text(encoding="utf-8")
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=text)


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    if path == "/sitemap.xml":
        body = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>http://example.com/</loc></url>
          <url><loc>http://example.com/about.html</loc></url>
          <url><loc>http://example.com/products.html</loc></url>
          <url><loc>http://example.com/contact.html</loc></url>
          <url><loc>http://example.com/orphan.html</loc></url>
        </urlset>
        """
        return httpx.Response(200, headers={"content-type": "application/xml"}, text=body)
    if path in {"/sitemap_index.xml", "/sitemap.xml.gz"}:
        return httpx.Response(404)
    if path == "/llms.txt":
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text=(_GOLDEN_E / "llms.txt").read_text(encoding="utf-8"),
        )
    mapping = {
        "/": "index.html",
        "/index.html": "index.html",
        "/about.html": "about.html",
        "/products.html": "products.html",
        "/contact.html": "contact.html",
        "/orphan.html": "orphan.html",
    }
    if path in mapping:
        return _html_file(mapping[path])
    if path == "/missing.html":
        return httpx.Response(404, headers={"content-type": "text/plain"}, text="missing")
    return httpx.Response(404)


def _client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(_handler),
        follow_redirects=False,
        trust_env=False,
    )


def _lighthouse_runner(url: str) -> dict:
    return {
        "categories": {
            "performance": {"score": 0.4},
            "accessibility": {"score": 0.9},
            "best-practices": {"score": 0.8},
            "seo": {"score": 0.7},
        },
        "audits": {},
    }


def test_pipeline_extracts_golden_e_and_stores_labelled_lighthouse() -> None:
    settings = get_settings().model_copy(
        update={
            "crawl_max_urls": 20,
            "crawl_max_depth": 3,
            "crawl_render_max_pages": 0,
            "crawl_lighthouse_max_pages": 1,
        }
    )
    analyzed = analyze_site(
        "http://example.com/",
        settings=settings,
        client=_client(),
        lookup=_lookup,
        skip_render=True,
        lighthouse_runner=_lighthouse_runner,
    )
    urls = {page.url for page in analyzed.pages}
    assert any(url.endswith("/orphan.html") for url in urls)
    assert any(page.title == "Golden E" for page in analyzed.pages)
    assert analyzed.llms_txt is not None
    assert analyzed.llms_txt["state"] == "present"
    assert analyzed.lighthouse
    signal = analyzed.lighthouse[0]
    assert signal["provenance"] == PROVENANCE
    assert signal["provenance_label"] == PROVENANCE_LABEL
    assert "raw" not in signal
    assert signal["categories"]["seo"] == 0.7
