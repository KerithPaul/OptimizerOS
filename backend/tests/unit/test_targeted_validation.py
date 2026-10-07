"""Targeted validation route matching (step 7.6 verify, `[SPEC AGENTS.md §36]`).

`ProductRoute -> /products/[id] -> /products/a, /products/b` — a Next.js
dynamic route segment must match one crawled URL segment, and a catch-all
must match the remainder of the path, without ever matching an unrelated
route.
"""

from __future__ import annotations

from app.changes import targeted
from app.changes.targeted import _route_pattern, _url_path, resolve_affected_urls


def test_static_route_matches_exact_path() -> None:
    pattern = _route_pattern("/about")
    assert pattern.match("/about")
    assert not pattern.match("/about/team")


def test_dynamic_segment_matches_one_path_segment() -> None:
    pattern = _route_pattern("/products/[id]")
    assert pattern.match("/products/a")
    assert pattern.match("/products/running-shoes")
    assert not pattern.match("/products/a/reviews")
    assert not pattern.match("/products")


def test_catch_all_matches_remainder_of_path() -> None:
    pattern = _route_pattern("/docs/[...slug]")
    assert pattern.match("/docs/a")
    assert pattern.match("/docs/a/b/c")


def test_root_route() -> None:
    pattern = _route_pattern("/")
    assert pattern.match("/")


def test_url_path_strips_origin_and_query() -> None:
    assert _url_path("https://example.com/products/a?ref=x", "https://example.com") == "/products/a"
    assert _url_path("https://example.com/products/a#section", "https://example.com") == "/products/a"
    assert _url_path("https://example.com", "https://example.com") == "/"


def test_falls_back_to_findings_affected_url_when_no_route_is_reachable(monkeypatch) -> None:
    """`client/index.html` (a Vite/wouter entry point, not Next.js app-router)
    has no `Route` node reachable from it — the graph walk comes up empty,
    same as it would for any static HTML entry point. Rather than skipping
    browser/SEO validation outright, fall back to the one page the finding
    was actually raised against.
    """

    monkeypatch.setattr(targeted, "_affected_routes", lambda *a, **k: ([], None))

    result = resolve_affected_urls(
        db=None,
        project_id=1,
        repository_id=1,
        website_id=None,
        changed_files=["client/index.html"],
        fallback_url="https://lexfintech.io/",
    )

    assert result.urls == ["https://lexfintech.io/"]
    assert any("no Route node reachable" in gap for gap in result.gaps)
    assert any("fallback" in gap for gap in result.gaps)
    assert not any("browser validation skipped" in gap for gap in result.gaps)


def test_no_fallback_leaves_urls_empty_and_gap_explicit(monkeypatch) -> None:
    monkeypatch.setattr(targeted, "_affected_routes", lambda *a, **k: ([], None))

    result = resolve_affected_urls(
        db=None,
        project_id=1,
        repository_id=1,
        website_id=None,
        changed_files=["client/index.html"],
    )

    assert result.urls == []
    assert any("no Route node reachable" in gap for gap in result.gaps)


def test_graph_error_gap_also_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(
        targeted, "_affected_routes", lambda *a, **k: ([], "code graph unavailable: boom")
    )

    result = resolve_affected_urls(
        db=None,
        project_id=1,
        repository_id=1,
        website_id=None,
        changed_files=["client/index.html"],
        fallback_url="https://lexfintech.io/",
    )

    assert result.urls == ["https://lexfintech.io/"]
    assert any("code graph unavailable" in gap for gap in result.gaps)
