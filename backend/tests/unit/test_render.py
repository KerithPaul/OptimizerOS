"""Playwright renderer (step 3.C.2 verify).

A page whose title is set by JavaScript must show different raw vs
rendered hashes, and the rendered title wins.
"""

import pytest

from app.intelligence.website.extract import extract_page
from app.intelligence.website.render import RenderResult, Renderer, apply_render

_JS_TITLE_HTML = """<!DOCTYPE html>
<html>
  <head><title>Raw Title</title></head>
  <body>
    <p>static</p>
    <script>document.title = "Rendered Title";</script>
  </body>
</html>
"""


def _renderer_or_skip() -> Renderer:
    renderer = Renderer()
    probe = renderer.render_html(
        "<html><body>ok</body></html>", url="https://public.example/probe"
    )
    if probe.state == "unavailable":
        renderer.close()
        pytest.skip(probe.message or "Playwright chromium is unavailable")
    return renderer


def test_js_title_changes_hash_and_rendered_title_wins() -> None:
    extracted = extract_page(_JS_TITLE_HTML, url="https://public.example/js")
    assert extracted.page.title == "Raw Title"
    raw_hash = extracted.page.raw_html_hash
    assert raw_hash

    renderer = _renderer_or_skip()
    try:
        result = renderer.render_html(
            _JS_TITLE_HTML,
            url="https://public.example/js",
            raw_html_hash=raw_hash,
        )
    finally:
        renderer.close()

    assert result.state == "observed", result.message
    assert result.rendered_title == "Rendered Title"
    assert result.rendered_dom_hash is not None
    assert result.rendered_dom_hash != raw_hash
    assert result.raw_to_rendered_changed is True

    merged = apply_render(extracted.page, result)
    assert merged.title == "Rendered Title"
    assert merged.rendered_dom_hash == result.rendered_dom_hash


def test_private_url_is_blocked_not_a_crash() -> None:
    renderer = Renderer()
    try:
        result = renderer.render_url("http://127.0.0.1/secret")
    finally:
        renderer.close()
    assert result.state == "blocked"


def test_preview_loopback_origin_is_not_ssrf_blocked() -> None:
    renderer = Renderer(preview_base_url="http://127.0.0.1:59999")
    try:
        result = renderer.render_url("http://127.0.0.1:59999/")
    finally:
        renderer.close()
    assert result.state != "blocked"


def test_apply_render_reextracts_js_injected_headings() -> None:
    raw = extract_page(
        "<html><body><p>shell</p></body></html>",
        url="https://example.com/app",
        status_code=200,
        sitemap_member=True,
    )
    rendered = """
    <html><body>
      <h1>Hydrated title</h1>
      <p>Client-rendered article body that search engines executing JavaScript would see.</p>
    </body></html>
    """
    result = RenderResult(
        url="https://example.com/app",
        state="observed",
        rendered_html=rendered,
        rendered_title="Hydrated title",
        rendered_dom_hash="dom-hash",
        rendered_text="Hydrated title Client-rendered",
        raw_to_rendered_changed=True,
    )
    merged = apply_render(raw.page, result)
    assert merged.title == "Hydrated title"
    assert any(heading.level == 1 and heading.text == "Hydrated title" for heading in merged.headings)
    assert merged.status_code == 200
    assert merged.sitemap_member is True
    assert merged.raw_html_hash == raw.page.raw_html_hash


def test_preview_allowlist_does_not_open_other_loopback_ports() -> None:
    renderer = Renderer(preview_base_url="http://127.0.0.1:59999")
    try:
        result = renderer.render_url("http://127.0.0.1:80/secret")
    finally:
        renderer.close()
    assert result.state == "blocked"
