"""Rank Math adapter. Headless getHead is read-only; writes use registered post meta.

A Rank Math-only field on a Yoast site never reaches this adapter. A
meta key that is not in the REST schema fails here instead of being
silently dropped by WordPress.
"""

from __future__ import annotations

from typing import Any

from app.connectors.wordpress.adapters.base import (
    RANKMATH_META_KEYS,
    RANKMATH_RAW_KEYS,
    YOAST_RAW_KEYS,
    AdapterFieldError,
    FieldWrite,
    meta_schema_properties,
)
from app.connectors.wordpress.adapters.core import resource_rest_path
from app.connectors.wordpress.rest import WordPressRest


class RankMathAdapter:
    name = "rankmath"

    def owns(self, field: str) -> bool:
        if field in YOAST_RAW_KEYS:
            return False
        return field in RANKMATH_RAW_KEYS or field in RANKMATH_META_KEYS

    def read(self, resource: dict[str, Any]) -> dict[str, Any]:
        meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
        return {
            "seo_title": meta.get("rank_math_title"),
            "meta_description": meta.get("rank_math_description"),
            "canonical": meta.get("rank_math_canonical_url"),
            "focus_keyword": meta.get("rank_math_focus_keyword"),
            "rank_math_head": resource.get("rank_math_head"),
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
        if field in YOAST_RAW_KEYS:
            raise AdapterFieldError(
                "Yoast-only field cannot be written through the Rank Math adapter"
            )
        if not self.owns(field):
            raise AdapterFieldError(f"Rank Math adapter does not own field {field!r}")
        meta_key = RANKMATH_META_KEYS.get(field, field)
        if meta_key not in RANKMATH_META_KEYS.values():
            raise AdapterFieldError(f"Rank Math adapter does not own field {field!r}")
        registered = meta_schema_properties(schema)
        if meta_key not in registered:
            raise AdapterFieldError(
                f"Rank Math field {meta_key} is not registered on this site's REST schema "
                "(Rank Math getHead is read-only; the key must be show_in_rest)"
            )
        return FieldWrite(
            field=field,
            rest_path=resource_rest_path(resource),
            payload={"meta": {meta_key: value}},
            meta_key=meta_key,
        )
