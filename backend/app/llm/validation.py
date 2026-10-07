"""Structured LLM output validation (step 2.A.3).

Every LLM response is parsed into a caller-supplied Pydantic model
(AGENTS.md §54). Malformed output is rejected, retried within
LLM_MAX_RETRIES, then failed explicitly. A malformed body is never
coerced into a partially-valid object.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = logging.getLogger("architectos.llm.validation")

_RAW_LOG_TRUNCATE = 2000
# GLM-5.3 and several other models ignore json_object mode and wrap the
# object in a ```json fence, sometimes with commentary around it.
_FENCE_RE = re.compile(r"```(?:json)?\s*\r?\n?(.*?)```", re.DOTALL | re.IGNORECASE)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(Exception):
    """LLM output was not valid JSON for the requested schema.

    Raising this after retries are exhausted is the explicit failure
    (AGENTS.md §65). Callers must not catch it and report success.
    """

    def __init__(
        self,
        message: str,
        *,
        raw: str | None = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.raw = raw
        self.attempts = attempts


def _json_candidate(raw: str) -> str:
    """Return the JSON text to decode, stripping common LLM wrappers.

    Schema validation stays strict (`strict=True`, no field coercion). This
    only recovers a JSON value that the model already produced inside a
    markdown fence or with leading/trailing commentary.
    """
    text = raw.strip()
    if not text:
        return text
    match = _FENCE_RE.search(text)
    if match:
        inner = match.group(1).strip()
        if inner:
            text = inner
    if text[:1] in "{[":
        return text
    start = text.find("{")
    if start < 0:
        return text
    decoder = json.JSONDecoder()
    try:
        _, end = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        return text
    return text[start : start + end]


def parse_structured(raw: str, schema: type[T]) -> T:
    """Parse `raw` as JSON and validate it as `schema`.

    Strict: JSON decode, then Pydantic `strict=True`. No type coercion
    and no filling-in of missing required fields. Markdown fences and
    surrounding commentary are stripped only so a complete JSON object
    the model already produced can be decoded.
    """
    if not (raw or "").strip():
        raise StructuredOutputError("LLM output is empty", raw=raw)

    candidate = _json_candidate(raw)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"LLM output is not valid JSON: {exc.msg}",
            raw=raw,
        ) from exc

    if not isinstance(payload, dict):
        raise StructuredOutputError(
            "LLM output JSON must be an object",
            raw=raw,
        )

    try:
        return schema.model_validate(payload, strict=True)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err.get('loc', ()))}: {err.get('msg')}"
            for err in exc.errors()
        )
        raise StructuredOutputError(
            f"LLM output JSON does not match the required schema ({details})",
            raw=raw,
        ) from exc


def parse_structured_with_retry(
    produce: Callable[[], str],
    schema: type[T],
    *,
    max_retries: int | None = None,
    on_failure: Callable[[StructuredOutputError], None] | None = None,
) -> T:
    """Call `produce`, parse, and retry only on malformed output.

    `max_retries` defaults to `LLM_MAX_RETRIES`. Total attempts are
    `max_retries + 1`. Non-`StructuredOutputError` exceptions from
    `produce` propagate immediately — those are not malformed output.
    `on_failure`, when given, runs after a malformed attempt that will
    be retried so the next `produce` can include the validation error.
    """
    if max_retries is None:
        max_retries = get_settings().llm_max_retries
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")

    attempts = max_retries + 1
    last_error: StructuredOutputError | None = None
    for attempt in range(attempts):
        raw = produce()
        try:
            return parse_structured(raw, schema)
        except StructuredOutputError as exc:
            last_error = exc
            if attempt < attempts - 1 and on_failure is not None:
                on_failure(exc)

    assert last_error is not None
    raw_snippet = (last_error.raw or "")[:_RAW_LOG_TRUNCATE]
    logger.warning(
        "structured LLM output malformed after %s attempts (schema=%s): %s | raw=%r",
        attempts,
        schema.__name__,
        last_error,
        raw_snippet,
    )
    raise StructuredOutputError(
        f"malformed LLM output after {attempts} attempts: {last_error}",
        raw=last_error.raw,
        attempts=attempts,
    ) from last_error
