"""Adapter boundary. A field the plugin does not own fails here, not silently."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from app.connectors.wordpress.rest import WordPressRest


class SeoPlugin(str, Enum):
    NONE = "none"
    YOAST = "yoast"
    RANKMATH = "rankmath"
    BOTH = "both"


class AdapterFieldError(Exception):
    """A field is not owned by this adapter, or is not REST-writable here."""


@dataclass(frozen=True)
class FieldWrite:
    field: str
    rest_path: str
    payload: dict[str, Any]
    meta_key: str | None = None


class WordPressFieldAdapter(Protocol):
    name: str

    def owns(self, field: str) -> bool: ...

    def read(self, resource: dict[str, Any]) -> dict[str, Any]: ...

    def plan_write(
        self,
        rest: WordPressRest,
        resource: dict[str, Any],
        field: str,
        value: Any,
        *,
        schema: dict[str, Any] | None,
    ) -> FieldWrite: ...


CORE_FIELDS = frozenset(
    {
        "content",
        "title",
        "excerpt",
        "alt_text",
        "featured_media",
        "categories",
        "tags",
    }
)

YOAST_META_KEYS = {
    "seo_title": "_yoast_wpseo_title",
    "meta_description": "_yoast_wpseo_metadesc",
    "canonical": "_yoast_wpseo_canonical",
    "focus_keyword": "_yoast_wpseo_focuskw",
    "noindex": "_yoast_wpseo_meta-robots-noindex",
}

RANKMATH_META_KEYS = {
    "seo_title": "rank_math_title",
    "meta_description": "rank_math_description",
    "canonical": "rank_math_canonical_url",
    "focus_keyword": "rank_math_focus_keyword",
}

YOAST_RAW_KEYS = frozenset(YOAST_META_KEYS.values())
RANKMATH_RAW_KEYS = frozenset(RANKMATH_META_KEYS.values())
SEO_ALIASES = frozenset(YOAST_META_KEYS) | frozenset(RANKMATH_META_KEYS)
YOAST_ONLY_FIELDS = YOAST_RAW_KEYS
RANKMATH_ONLY_FIELDS = RANKMATH_RAW_KEYS


def meta_schema_properties(schema: dict[str, Any] | None) -> set[str]:
    if not schema:
        return set()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return set()
    meta = properties.get("meta")
    if not isinstance(meta, dict):
        return set()
    meta_props = meta.get("properties")
    if not isinstance(meta_props, dict):
        return set()
    return set(meta_props)


def wordpress_adapter(plugin: SeoPlugin) -> WordPressFieldAdapter:
    from app.connectors.wordpress.adapters.rankmath import RankMathAdapter
    from app.connectors.wordpress.adapters.yoast import YoastAdapter

    if plugin is SeoPlugin.RANKMATH:
        return RankMathAdapter()
    if plugin is SeoPlugin.YOAST:
        return YoastAdapter()
    if plugin is SeoPlugin.BOTH:
        return YoastAdapter()
    return YoastAdapter()
