"""Yoast SEO adapter. Official Yoast REST is read-only; writes use registered post meta.

A Yoast-only field on a Rank Math site never reaches this adapter. A
meta key that is not in the REST schema fails here instead of being
silently dropped by WordPress.
"""

from __future__ import annotations

from typing import Any

from app.connectors.wordpress.adapters.base import (
    RANKMATH_RAW_KEYS,
    YOAST_META_KEYS,
    YOAST_RAW_KEYS,
    AdapterFieldError,
    FieldWrite,
    meta_schema_properties,
)
from app.connectors.wordpress.adapters.core import resource_rest_path
from app.connectors.wordpress.rest import WordPressRest


def _from_yoast_head(resource: dict[str, Any]) -> dict[str, Any]:
    head = resource.get("yoast_head_json")
    if not isinstance(head, dict):
        return {}
    robots = head.get("robots") if isinstance(head.get("robots"), dict) else {}
    return {
        "seo_title": head.get("title"),
        "meta_description": head.get("description"),
        "canonical": head.get("canonical"),
        "robots": robots,
        "og": {
            key: head[key]
            for key in ("og_title", "og_description", "og_url", "og_image", "og_type")
            if key in head
        },
        "schema": head.get("schema"),
    }


class YoastAdapter:
    name = "yoast"

    def owns(self, field: str) -> bool:
        if field in RANKMATH_RAW_KEYS:
            return False
        return field in YOAST_RAW_KEYS or field in YOAST_META_KEYS

    def read(self, resource: dict[str, Any]) -> dict[str, Any]:
        meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
        head = _from_yoast_head(resource)
        return {
            "seo_title": meta.get("_yoast_wpseo_title") or head.get("seo_title"),
            "meta_description": meta.get("_yoast_wpseo_metadesc")
            or head.get("meta_description"),
            "canonical": meta.get("_yoast_wpseo_canonical") or head.get("canonical"),
            "focus_keyword": meta.get("_yoast_wpseo_focuskw"),
            "noindex": meta.get("_yoast_wpseo_meta-robots-noindex"),
            "yoast_head_json": resource.get("yoast_head_json"),
            "schema": head.get("schema"),
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
        del rest
        if field in RANKMATH_RAW_KEYS:
            raise AdapterFieldError(
                "Rank Math-only field cannot be written through the Yoast adapter"
            )
        if not self.owns(field):
            raise AdapterFieldError(f"Yoast adapter does not own field {field!r}")
        meta_key = YOAST_META_KEYS.get(field, field)
        if meta_key not in YOAST_META_KEYS.values():
            raise AdapterFieldError(f"Yoast adapter does not own field {field!r}")
        registered = meta_schema_properties(schema)
        if meta_key not in registered:
            raise AdapterFieldError(
                f"Yoast field {meta_key} is not registered on this site's REST schema "
                "(Yoast's own REST API is read-only; the key must be show_in_rest)"
            )
        return FieldWrite(
            field=field,
            rest_path=resource_rest_path(resource),
            payload={"meta": {meta_key: value}},
            meta_key=meta_key,
        )
