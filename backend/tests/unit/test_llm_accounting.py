"""Per-call LLM accounting attached to a job (step 2.A.4 verify)."""

from pydantic import ValidationError
import pytest

from app.llm.gateway import LLMCallRecord, record_llm_call


class _FakeJob:
    def __init__(self) -> None:
        self.id = 1
        self.llm_usage_json = None


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_llm_step_records_nonzero_token_usage_on_the_job() -> None:
    job = _FakeJob()
    db = _FakeSession()

    record_llm_call(
        db,  # type: ignore[arg-type]
        job,  # type: ignore[arg-type]
        LLMCallRecord(provider="groq", model="test-small", tokens=37, latency_ms=12),
    )

    assert db.commits == 1
    assert job.llm_usage_json is not None
    assert len(job.llm_usage_json) == 1
    recorded = job.llm_usage_json[0]
    assert recorded["tokens"] == 37
    assert recorded["tokens"] > 0
    assert recorded["provider"] == "groq"
    assert recorded["model"] == "test-small"
    assert recorded["latency_ms"] == 12


def test_subsequent_calls_append_on_the_same_job() -> None:
    job = _FakeJob()
    db = _FakeSession()

    record_llm_call(
        db,  # type: ignore[arg-type]
        job,  # type: ignore[arg-type]
        LLMCallRecord(provider="groq", model="test-small", tokens=10, latency_ms=8),
    )
    record_llm_call(
        db,  # type: ignore[arg-type]
        job,  # type: ignore[arg-type]
        LLMCallRecord(provider="freellm", model="test-strong", tokens=22, latency_ms=40),
    )

    assert db.commits == 2
    assert job.llm_usage_json is not None
    assert [c["tokens"] for c in job.llm_usage_json] == [10, 22]
    assert [c["provider"] for c in job.llm_usage_json] == ["groq", "freellm"]


def test_negative_tokens_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LLMCallRecord(provider="groq", model="test-small", tokens=-1, latency_ms=1)
