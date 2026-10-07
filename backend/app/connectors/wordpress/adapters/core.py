"""Core WordPress REST fields. No Yoast or Rank Math keys live here."""

from __future__ import annotations

from typing import Any

from app.connectors.wordpress.adapters.base import (
    CORE_FIELDS,
    RANKMATH_RAW_KEYS,
    SEO_ALIASES,
    YOAST_RAW_KEYS,
    AdapterFieldError,
    FieldWrite,
)
from app.connectors.wordpress.rest import WordPressRest


def _rendered_or_raw(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("raw")
        if isinstance(raw, str):
            return raw
        rendered = value.get("rendered")
        if isinstance(rendered, str):
            return rendered
        return None
    if isinstance(value, str):
        return value
    return None


def resource_rest_path(resource: dict[str, Any]) -> str:
    rest_base = resource.get("_rest_base")
    ident = resource.get("id")
    if not rest_base or ident is None:
        raise AdapterFieldError("WordPress resource is missing REST identity")
    return f"/wp-json/wp/v2/{rest_base}/{ident}"


class CoreAdapter:
    name = "core"

    def owns(self, field: str) -> bool:
        if field in YOAST_RAW_KEYS or field in RANKMATH_RAW_KEYS or field in SEO_ALIASES:
            return False
        return field in CORE_FIELDS

    def read(self, resource: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": _rendered_or_raw(resource.get("content")),
            "title": _rendered_or_raw(resource.get("title")),
            "excerpt": _rendered_or_raw(resource.get("excerpt")),
            "alt_text": resource.get("alt_text")
            if isinstance(resource.get("alt_text"), str)
            else None,
            "featured_media": resource.get("featured_media"),
            "categories": resource.get("categories"),
            "tags": resource.get("tags"),
        }

    def plan_write(
        self,
        rest: WordPressRest,
        resource: dict[str, Any],
        field: str,
        value: Any,
        *,
        schema: dict[str, Any] | None,
    ) -> FieldWrite:
        del rest, schema
        if field in YOAST_RAW_KEYS or field in RANKMATH_RAW_KEYS or field in SEO_ALIASES:
            raise AdapterFieldError(
                "plugin SEO fields cannot be written through the core WordPress adapter"
            )
        if not self.owns(field):
            raise AdapterFieldError(f"core WordPress adapter does not own field {field!r}")

        if field == "alt_text":
            ident = resource.get("id")
            if resource.get("_rest_base") != "media" or ident is None:
                featured = resource.get("featured_media")
                if not featured:
                    raise AdapterFieldError("resource has no featured media to update alt_text")
                path = f"/wp-json/wp/v2/media/{featured}"
            else:
                path = f"/wp-json/wp/v2/media/{ident}"
            return FieldWrite(
                field=field, rest_path=path, payload={"alt_text": str(value)}
            )

        return FieldWrite(
            field=field,
            rest_path=resource_rest_path(resource),
            payload={field: value},
        )
