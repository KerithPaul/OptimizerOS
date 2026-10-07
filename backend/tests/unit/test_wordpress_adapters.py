"""Yoast vs Rank Math adapter boundary (step 10.2 verify)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.wordpress.adapters.base import AdapterFieldError
from app.connectors.wordpress.connector import WordPressConnector

YOAST_SCHEMA = {
    "schema": {
        "properties": {
            "meta": {
                "properties": {
                    "_yoast_wpseo_title": {"type": "string"},
                    "_yoast_wpseo_metadesc": {"type": "string"},
                    "_yoast_wpseo_canonical": {"type": "string"},
                }
            }
        }
    }
}

RANKMATH_SCHEMA = {
    "schema": {
        "properties": {
            "meta": {
                "properties": {
                    "rank_math_title": {"type": "string"},
                    "rank_math_description": {"type": "string"},
                    "rank_math_canonical_url": {"type": "string"},
                }
            }
        }
    }
}


def _page(plugin: str, *, meta: dict | None = None) -> dict:
    body = {
        "id": 12,
        "link": "https://golden-c.example/about/",
        "title": {"raw": "Golden C", "rendered": "Golden C"},
        "content": {
            "raw": "<p>About</p>",
            "rendered": "<p>About</p>",
        },
        "excerpt": {"raw": "", "rendered": ""},
        "status": "publish",
        "featured_media": 0,
        "meta": meta or {},
    }
    if plugin == "yoast":
        body["yoast_head_json"] = {"title": "Golden C", "description": None}
    return body


def _handler(plugin: str, store: dict):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in {"/wp-json", "/wp-json/"}:
            namespaces = ["wp/v2"]
            if plugin == "yoast":
                namespaces.append("yoast/v1")
            if plugin == "rankmath":
                namespaces.append("rankmath/v1")
            return httpx.Response(200, json={"namespaces": namespaces, "name": "Golden"})
        if path == "/wp-json/wp/v2/users/me":
            auth = request.headers.get("authorization")
            if not auth:
                return httpx.Response(401, json={"code": "rest_not_logged_in"})
            return httpx.Response(200, json={"id": 1, "name": "admin"})
        if path == "/wp-json/wp/v2/types":
            return httpx.Response(
                200,
                json={
                    "page": {"rest_base": "pages", "name": "page"},
                    "post": {"rest_base": "posts", "name": "post"},
                },
            )
        if path == "/wp-json/wp/v2/plugins":
            return httpx.Response(403, json={"code": "rest_forbidden"})
        if path.endswith("/pages") and request.method == "OPTIONS":
            schema = YOAST_SCHEMA if plugin == "yoast" else RANKMATH_SCHEMA
            return httpx.Response(200, json=schema)
        if path == "/wp-json/wp/v2/pages":
            return httpx.Response(
                200,
                headers={"X-WP-TotalPages": "1"},
                json=[store["page"]],
            )
        if path == "/wp-json/wp/v2/posts":
            return httpx.Response(200, headers={"X-WP-TotalPages": "1"}, json=[])
        if path == "/wp-json/wp/v2/media":
            return httpx.Response(200, headers={"X-WP-TotalPages": "1"}, json=[])
        if path == "/wp-json/wp/v2/pages/12":
            if request.method == "POST":
                payload = json.loads(request.content.decode())
                if "meta" in payload:
                    store["page"].setdefault("meta", {}).update(payload["meta"])
                if "content" in payload:
                    value = payload["content"]
                    store["page"]["content"] = {"raw": value, "rendered": value}
                if "title" in payload:
                    value = payload["title"]
                    store["page"]["title"] = {"raw": value, "rendered": value}
            return httpx.Response(200, json=store["page"])
        return httpx.Response(404, json={"code": "rest_no_route"})

    return handle


def _connector(plugin: str, store: dict) -> WordPressConnector:
    client = httpx.Client(
        transport=httpx.MockTransport(_handler(plugin, store)),
        follow_redirects=False,
        trust_env=False,
    )
    origin = (
        "https://golden-c.example/" if plugin == "yoast" else "https://golden-d.example/"
    )
    connector = WordPressConnector.from_credentials(
        origin,
        "admin",
        "aaaa bbbb cccc dddd",
        client=client,
        lookup=lambda host: ["93.184.216.34"],
        project_id=1,
    )
    connector.authenticate()
    return connector


def test_yoast_seo_title_writes_yoast_meta_key() -> None:
    store = {"page": _page("yoast")}
    connector = _connector("yoast", store)
    connector.update_metadata(
        "https://golden-c.example/about/", {"seo_title": "About | Golden C"}
    )
    assert store["page"]["meta"]["_yoast_wpseo_title"] == "About | Golden C"


def test_yoast_field_on_rankmath_site_fails_at_adapter() -> None:
    store = {"page": _page("rankmath")}
    connector = _connector("rankmath", store)
    with pytest.raises(AdapterFieldError, match="Yoast-only"):
        connector.update_metadata(
            "https://golden-c.example/about/",
            {"_yoast_wpseo_title": "should not write"},
        )
    assert "_yoast_wpseo_title" not in store["page"].get("meta", {})


def test_rankmath_field_on_yoast_site_fails_at_adapter() -> None:
    store = {"page": _page("yoast")}
    connector = _connector("yoast", store)
    with pytest.raises(AdapterFieldError, match="Rank Math-only"):
        connector.update_metadata(
            "https://golden-c.example/about/",
            {"rank_math_title": "should not write"},
        )


def test_unregistered_yoast_meta_fails_explicitly() -> None:
    store = {"page": _page("yoast")}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pages") and request.method == "OPTIONS":
            return httpx.Response(200, json={"schema": {"properties": {}}})
        return _handler("yoast", store)(request)

    client = httpx.Client(
        transport=httpx.MockTransport(handle),
        follow_redirects=False,
        trust_env=False,
    )
    connector = WordPressConnector.from_credentials(
        "https://golden-c.example/",
        "admin",
        "token",
        client=client,
        lookup=lambda host: ["93.184.216.34"],
        project_id=1,
    )
    connector.authenticate()
    with pytest.raises(AdapterFieldError, match="not registered"):
        connector.update_metadata(
            "https://golden-c.example/about/", {"seo_title": "Nope"}
        )


def test_core_content_write_does_not_touch_plugin_meta() -> None:
    store = {"page": _page("yoast", meta={"_yoast_wpseo_title": "Keep"})}
    connector = _connector("yoast", store)
    connector.update_content("https://golden-c.example/about/", "<p>Updated</p>")
    assert store["page"]["content"]["raw"] == "<p>Updated</p>"
    assert store["page"]["meta"]["_yoast_wpseo_title"] == "Keep"


def test_auth_failure_is_explicit() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/wp-json", "/wp-json/"}:
            return httpx.Response(200, json={"namespaces": ["wp/v2"], "name": "X"})
        return httpx.Response(401, json={"code": "rest_not_logged_in"})

    client = httpx.Client(
        transport=httpx.MockTransport(handle),
        follow_redirects=False,
        trust_env=False,
    )
    from app.connectors.wordpress.auth import WordPressAuthError

    connector = WordPressConnector.from_credentials(
        "https://golden-c.example/",
        "admin",
        "bad",
        client=client,
        lookup=lambda host: ["93.184.216.34"],
    )
    with pytest.raises(WordPressAuthError, match="authentication failed"):
        connector.authenticate()


def test_non_wordpress_index_is_api_failure() -> None:
    from app.connectors.wordpress.rest import WordPressApiError

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "wordpress"})

    client = httpx.Client(
        transport=httpx.MockTransport(handle),
        follow_redirects=False,
        trust_env=False,
    )
    connector = WordPressConnector.from_credentials(
        "https://golden-c.example/",
        "admin",
        "token",
        client=client,
        lookup=lambda host: ["93.184.216.34"],
    )
    with pytest.raises(WordPressApiError, match="not a WordPress REST index"):
        connector.authenticate()


def test_ingest_maps_to_common_website_model() -> None:
    store = {"page": _page("yoast")}
    connector = _connector("yoast", store)
    pages = connector.discover()
    assert pages
    assert pages[0].url == "https://golden-c.example/about/"
    assert pages[0].title == "Golden C"
    dumped = pages[0].model_dump()
    assert "yoast_head_json" not in dumped
    assert "_yoast_wpseo_title" not in dumped
