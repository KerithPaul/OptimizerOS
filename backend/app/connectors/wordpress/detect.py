"""Detect WordPress and the installed SEO plugin from the REST index.

Uses namespaces and registered REST fields. `/wp/v2/plugins` requires
admin caps that an Application Password may not have, so it is optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.connectors.wordpress.adapters.base import SeoPlugin
from app.connectors.wordpress.auth import WordPressAuthError
from app.connectors.wordpress.rest import WordPressApiError, WordPressRest


@dataclass
class WordPressDetection:
    is_wordpress: bool
    namespaces: list[str]
    seo_plugin: SeoPlugin
    site_name: str | None = None
    language: str | None = None
    types: list[dict[str, Any]] = field(default_factory=list)
    revisions_available: bool = False
    plugins_endpoint_available: bool = False
    plugin_slugs: list[str] = field(default_factory=list)


def detect_wordpress(rest: WordPressRest) -> WordPressDetection:
    try:
        index = rest.get_json("/wp-json/")
    except WordPressApiError as exc:
        raise WordPressApiError(
            f"site is not a WordPress REST index: {exc}", status_code=exc.status_code
        ) from exc
    if not isinstance(index, dict):
        raise WordPressApiError("site is not a WordPress REST index")
    namespaces = index.get("namespaces")
    if not isinstance(namespaces, list) or "wp/v2" not in namespaces:
        raise WordPressApiError("site is not a WordPress REST index")
    ns = [str(item) for item in namespaces]
    name = None
    gmt_offset = index.get("gmt_offset")
    del gmt_offset
    site_name = index.get("name")
    if isinstance(site_name, str):
        name = site_name

    has_yoast = "yoast/v1" in ns
    has_rankmath = "rankmath/v1" in ns
    plugin_slugs: list[str] = []
    plugins_ok = False
    try:
        plugins = rest.get_json("/wp-json/wp/v2/plugins")
        if isinstance(plugins, list):
            plugins_ok = True
            for row in plugins:
                if not isinstance(row, dict):
                    continue
                plugin = str(row.get("plugin") or "")
                status = str(row.get("status") or "")
                if status != "active":
                    continue
                plugin_slugs.append(plugin)
                if plugin.startswith("wordpress-seo/"):
                    has_yoast = True
                if plugin.startswith("seo-by-rank-math/"):
                    has_rankmath = True
    except (WordPressApiError, WordPressAuthError):
        plugins_ok = False

    if has_yoast and has_rankmath:
        seo_plugin = SeoPlugin.BOTH
    elif has_yoast:
        seo_plugin = SeoPlugin.YOAST
    elif has_rankmath:
        seo_plugin = SeoPlugin.RANKMATH
    else:
        seo_plugin = SeoPlugin.NONE

    types: list[dict[str, Any]] = []
    try:
        raw_types = rest.get_json("/wp-json/wp/v2/types")
        if isinstance(raw_types, dict):
            types = [
                {"name": key, **value} if isinstance(value, dict) else {"name": key}
                for key, value in raw_types.items()
            ]
    except WordPressApiError:
        types = []

    revisions_available = False
    try:
        rest.request("GET", "/wp-json/wp/v2/pages", params={"per_page": 1, "context": "edit"})
        revisions_available = True
    except (WordPressApiError, WordPressAuthError):
        revisions_available = False

    return WordPressDetection(
        is_wordpress=True,
        namespaces=ns,
        seo_plugin=seo_plugin,
        site_name=name,
        types=types,
        revisions_available=revisions_available,
        plugins_endpoint_available=plugins_ok,
        plugin_slugs=plugin_slugs,
    )
