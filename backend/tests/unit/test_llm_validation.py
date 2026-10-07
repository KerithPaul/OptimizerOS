"""Structured LLM output validation (step 2.A.3 verify)."""

import pytest
from pydantic import BaseModel

from app.llm.validation import (
    StructuredOutputError,
    parse_structured,
    parse_structured_with_retry,
)


class _SampleOut(BaseModel):
    title: str
    confidence: float


def test_valid_json_parses_into_the_model() -> None:
    result = parse_structured(
        '{"title": "Home", "confidence": 0.9}',
        _SampleOut,
    )
    assert result == _SampleOut(title="Home", confidence=0.9)


def test_malformed_json_exhausts_retries_then_fails() -> None:
    calls = {"n": 0}

    def produce() -> str:
        calls["n"] += 1
        return "this is not json"

    with pytest.raises(StructuredOutputError) as exc_info:
        parse_structured_with_retry(produce, _SampleOut, max_retries=2)

    assert calls["n"] == 3
    assert exc_info.value.attempts == 3
    assert "malformed LLM output after 3 attempts" in str(exc_info.value)
    assert exc_info.value.raw == "this is not json"


def test_succeeds_on_a_later_attempt() -> None:
    responses = iter(["this is not json", '{"title": "Home", "confidence": 0.5}'])

    result = parse_structured_with_retry(
        lambda: next(responses),
        _SampleOut,
        max_retries=2,
    )

    assert result == _SampleOut(title="Home", confidence=0.5)


def test_missing_required_field_is_not_coerced() -> None:
    with pytest.raises(StructuredOutputError, match="does not match the required schema"):
        parse_structured('{"title": "Home"}', _SampleOut)


def test_wrong_types_are_not_coerced() -> None:
    with pytest.raises(StructuredOutputError, match="does not match the required schema"):
        parse_structured('{"title": "Home", "confidence": "0.9"}', _SampleOut)


def test_non_object_json_is_malformed() -> None:
    with pytest.raises(StructuredOutputError, match="must be an object"):
        parse_structured('["Home", 0.9]', _SampleOut)


def test_markdown_fenced_json_is_accepted() -> None:
    raw = '```json\n{"title": "Home", "confidence": 0.9}\n```'
    result = parse_structured(raw, _SampleOut)
    assert result == _SampleOut(title="Home", confidence=0.9)


def test_json_object_with_trailing_commentary_is_accepted() -> None:
    raw = (
        '```json\n{"title": "Home", "confidence": 0.9}\n```\n\n'
        "Note: content was excluded because it is higher risk."
    )
    result = parse_structured(raw, _SampleOut)
    assert result.title == "Home"


def test_empty_output_is_malformed() -> None:
    with pytest.raises(StructuredOutputError, match="empty"):
        parse_structured("", _SampleOut)


def test_leading_prose_then_json_object_is_accepted() -> None:
    raw = 'Here is the plan:\n{"title": "Home", "confidence": 0.5}'
    result = parse_structured(raw, _SampleOut)
    assert result == _SampleOut(title="Home", confidence=0.5)


def test_producer_errors_are_not_treated_as_malformed_retries() -> None:
    calls = {"n": 0}

    def produce() -> str:
        calls["n"] += 1
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError, match="provider down"):
        parse_structured_with_retry(produce, _SampleOut, max_retries=2)

    assert calls["n"] == 1


def test_schema_mismatch_includes_field_path() -> None:
    with pytest.raises(StructuredOutputError, match="confidence"):
        parse_structured('{"title": "Home", "confidence": "0.9"}', _SampleOut)


def test_on_failure_runs_before_retry_not_after_last_attempt() -> None:
    failures: list[str] = []
    responses = iter(["not json", '{"title": "Home", "confidence": 0.5}'])

    def produce() -> str:
        return next(responses)

    result = parse_structured_with_retry(
        produce,
        _SampleOut,
        max_retries=2,
        on_failure=lambda exc: failures.append(str(exc)),
    )

    assert result.title == "Home"
    assert len(failures) == 1
    assert "not valid JSON" in failures[0]
