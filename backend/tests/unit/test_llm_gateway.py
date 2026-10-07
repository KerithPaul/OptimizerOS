"""FreeLLMAPI → Groq fallback and model selection (step 2.A.1 verify)."""

import json
from collections.abc import Iterator, Sequence

from cryptography.fernet import Fernet
import httpx
import pytest

from app.core.config import Settings
from app.llm.gateway import (
    CHARS_PER_TOKEN_ESTIMATE,
    ChatResult,
    FreeLLMAdapter,
    LLMError,
    LLMGateway,
    ModelTier,
    ProviderError,
    _Completion,
    _from_openai_json,
    _from_openai_shaped,
    cap_groq_max_tokens,
    estimate_input_tokens,
    freellm_api_base_url,
    groq_completion_budget,
    request_is_oversized,
)
from app.llm.validation import StructuredOutputError
from pydantic import BaseModel


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "FREELLM_API_KEY": "test-freellm",
        "FREELLM_MODEL": "auto",
        "GROQ_API_KEY": "test-groq",
        "GROQ_MODEL_SMALL": "groq-small",
        "GROQ_MODEL_STRONG": "groq-strong",
        "LLM_MAX_RETRIES": 1,
        "LLM_REQUEST_TIMEOUT_SECONDS": 5,
    }
    values.update(overrides)
    return Settings(**values)


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False, content: str = "ok", tokens: int = 11) -> None:
        self.name = name
        self.fail = fail
        self.content = content
        self.tokens = tokens
        self.complete_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def complete(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        tools: list[dict] | None,
        timeout: float,
        response_format: dict | None = None,
        max_tokens: int | None = None,
    ) -> _Completion:
        self.complete_calls.append(
            {
                "model": model,
                "tools": tools,
                "timeout": timeout,
                "messages": list(messages),
                "response_format": response_format,
                "max_tokens": max_tokens,
            }
        )
        if self.fail:
            raise ProviderError(f"{self.name} down")
        return _Completion(content=self.content, tokens=self.tokens, model=model)

    def stream(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        timeout: float,
    ) -> Iterator[str]:
        self.stream_calls.append({"model": model, "timeout": timeout})
        if self.fail:
            raise ProviderError(f"{self.name} down")
        yield "hel"
        yield "lo"


def test_forced_freellm_failure_falls_back_to_groq_and_records_provider() -> None:
    freellm = FakeProvider("freellm", fail=True)
    groq = FakeProvider("groq", content="from-groq", tokens=17)
    gateway = LLMGateway(_settings(), primary=freellm, fallback=groq)

    result = gateway.chat([{"role": "user", "content": "hi"}])

    assert isinstance(result, ChatResult)
    assert result.provider == "groq"
    assert result.model == "groq-small"
    assert result.content == "from-groq"
    assert result.tokens == 17
    assert freellm.complete_calls  # retried then fell back
    assert len(freellm.complete_calls) == 2  # LLM_MAX_RETRIES=1 → 2 attempts
    assert len(groq.complete_calls) == 1


def test_freellm_success_does_not_call_groq() -> None:
    freellm = FakeProvider("freellm", content="from-freellm", tokens=4)
    groq = FakeProvider("groq")
    gateway = LLMGateway(_settings(), primary=freellm, fallback=groq)

    result = gateway.chat([{"role": "user", "content": "hi"}])

    assert result.provider == "freellm"
    assert result.model == "auto"
    assert result.content == "from-freellm"
    assert groq.complete_calls == []


def test_strong_tier_uses_strong_model_ids() -> None:
    freellm = FakeProvider("freellm")
    groq = FakeProvider("groq")
    gateway = LLMGateway(_settings(), primary=freellm, fallback=groq)

    result = gateway.chat([{"role": "user", "content": "hi"}], tier=ModelTier.STRONG)

    assert result.provider == "freellm"
    assert freellm.complete_calls[0]["model"] == "auto"
    assert groq.complete_calls == []


def test_all_providers_failing_raises_explicitly() -> None:
    gateway = LLMGateway(
        _settings(),
        primary=FakeProvider("freellm", fail=True),
        fallback=FakeProvider("groq", fail=True),
    )
    with pytest.raises(LLMError, match="all LLM providers failed"):
        gateway.chat([{"role": "user", "content": "hi"}])


def test_stream_falls_back_to_groq() -> None:
    freellm = FakeProvider("freellm", fail=True)
    groq = FakeProvider("groq")
    gateway = LLMGateway(_settings(), primary=freellm, fallback=groq)

    text = "".join(gateway.chat_stream([{"role": "user", "content": "hi"}]))

    assert text == "hello"
    assert freellm.stream_calls
    assert groq.stream_calls


def test_structured_output_uses_the_answering_provider() -> None:
    class _Out(BaseModel):
        title: str

    freellm = FakeProvider("freellm", fail=True)
    groq = FakeProvider("groq", content='{"title": "Home"}')
    gateway = LLMGateway(_settings(), primary=freellm, fallback=groq)

    parsed = gateway.chat_structured([{"role": "user", "content": "hi"}], _Out)

    assert parsed.title == "Home"


def test_structured_malformed_output_is_not_coerced() -> None:
    class _Out(BaseModel):
        title: str

    freellm = FakeProvider("freellm", content="not-json")
    groq = FakeProvider("groq", content="also-not-json")
    gateway = LLMGateway(_settings(LLM_MAX_RETRIES=0), primary=freellm, fallback=groq)

    with pytest.raises(StructuredOutputError):
        gateway.chat_structured([{"role": "user", "content": "hi"}], _Out)


def test_chat_records_the_provider_that_answered_on_the_job() -> None:
    freellm = FakeProvider("freellm", fail=True)
    groq = FakeProvider("groq", tokens=21)
    gateway = LLMGateway(_settings(), primary=freellm, fallback=groq)

    class _Job:
        id = 7
        llm_usage_json = None

    class _Db:
        def commit(self) -> None:
            return None

    job = _Job()
    result = gateway.chat(
        [{"role": "user", "content": "hi"}],
        job=job,  # type: ignore[arg-type]
        db=_Db(),  # type: ignore[arg-type]
    )

    assert result.provider == "groq"
    assert job.llm_usage_json is not None
    assert job.llm_usage_json[0]["provider"] == "groq"
    assert job.llm_usage_json[0]["tokens"] == 21
    assert job.llm_usage_json[0]["tokens"] > 0


def test_empty_content_falls_back_to_next_provider() -> None:
    freellm = FakeProvider("freellm", content="")
    groq = FakeProvider("groq", content="from-groq", tokens=9)
    gateway = LLMGateway(_settings(LLM_MAX_RETRIES=0), primary=freellm, fallback=groq)

    result = gateway.chat([{"role": "user", "content": "hi"}])

    assert result.provider == "groq"
    assert result.content == "from-groq"
    assert freellm.complete_calls
    assert groq.complete_calls


def test_from_openai_json_uses_reasoning_content_when_content_is_null() -> None:
    parsed = _from_openai_json(
        {
            "model": "glm-5.3",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": '{"title": "Home"}',
                    }
                }
            ],
            "usage": {"total_tokens": 40},
        },
        "fallback",
    )
    assert parsed.content == '{"title": "Home"}'
    assert parsed.tokens == 40
    assert parsed.model == "glm-5.3"


def test_from_openai_json_joins_content_parts() -> None:
    parsed = _from_openai_json(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": '{"a":'},
                            {"type": "text", "text": " 1}"},
                        ],
                    }
                }
            ],
            "usage": {"total_tokens": 4},
        },
        "fallback-model",
    )
    assert parsed.content == '{"a": 1}'
    assert parsed.model == "fallback-model"


def test_from_openai_shaped_uses_reasoning_content() -> None:
    class _Msg:
        content = None
        reasoning_content = "visible from reasoning"
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Usage:
        total_tokens = 12

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()
        model = "glm-5.3"

    parsed = _from_openai_shaped(_Resp(), "fallback")
    assert parsed.content == "visible from reasoning"
    assert parsed.tokens == 12


def test_request_is_oversized_detects_413_and_tpm_requested_over_limit() -> None:
    assert request_is_oversized(ProviderError("nope", status_code=413)) is True
    assert request_is_oversized(
        LLMError("Error code: 413 - Request too large for endpoint.")
    ) is True
    assert request_is_oversized(
        ProviderError(
            "Rate limit reached on tokens per minute (TPM): Limit 6000, Requested 12788",
            status_code=429,
        )
    ) is True
    assert request_is_oversized(
        ProviderError("Rate limit reached on requests per minute (RPM): Limit 30", status_code=429)
    ) is False


class _StatusProvider(FakeProvider):
    def __init__(self, name: str, *, status_code: int, message: str) -> None:
        super().__init__(name, fail=True)
        self._status_code = status_code
        self._message = message

    def complete(self, messages, *, model, tools, timeout, response_format=None, max_tokens=None):
        self.complete_calls.append({"model": model, "max_tokens": max_tokens})
        raise ProviderError(self._message, status_code=self._status_code)


def test_oversized_413_is_not_retried_on_the_same_provider() -> None:
    freellm = _StatusProvider("freellm", status_code=413, message="Request too large for endpoint.")
    groq = FakeProvider("groq", content="from-groq", tokens=3)
    gateway = LLMGateway(_settings(LLM_MAX_RETRIES=2), primary=freellm, fallback=groq)

    result = gateway.chat([{"role": "user", "content": "hi"}])

    assert result.provider == "groq"
    assert len(freellm.complete_calls) == 1


def test_tpm_requested_over_limit_is_not_retried_on_the_same_provider() -> None:
    freellm = _StatusProvider(
        "freellm",
        status_code=429,
        message="Rate limit reached on tokens per minute (TPM): Limit 6000, Requested 12788",
    )
    groq = FakeProvider("groq", content="from-groq", tokens=3)
    gateway = LLMGateway(_settings(LLM_MAX_RETRIES=2), primary=freellm, fallback=groq)

    result = gateway.chat([{"role": "user", "content": "hi"}])

    assert result.provider == "groq"
    assert len(freellm.complete_calls) == 1


def test_chat_forwards_max_tokens_to_the_adapter() -> None:
    freellm = FakeProvider("freellm", content="ok", tokens=2)
    gateway = LLMGateway(_settings(), primary=freellm, fallback=FakeProvider("groq"))

    gateway.chat([{"role": "user", "content": "hi"}], max_tokens=1024)

    assert freellm.complete_calls[0]["max_tokens"] == 1024


def test_cap_groq_max_tokens_clamps_small_model_to_otpm_limit() -> None:
    settings = _settings(
        GROQ_MODEL_SMALL="qwen/qwen3.8-27b",
        GROQ_MODEL_STRONG="openai/gpt-oss-120b",
        GROQ_SMALL_OTPM_LIMIT=1000,
        LLM_MAX_TOKENS=4096,
    )
    assert cap_groq_max_tokens(settings, "qwen/qwen3.8-27b", None) == 1000
    assert cap_groq_max_tokens(settings, "qwen/qwen3.8-27b", 800) == 800
    assert cap_groq_max_tokens(settings, "qwen/qwen3.8-27b", 1000) == 1000
    assert cap_groq_max_tokens(settings, "openai/gpt-oss-120b", 4096) == 4096
    assert cap_groq_max_tokens(settings, "openai/gpt-oss-120b", None) == 4096


def _groq_settings() -> Settings:
    return _settings(
        GROQ_MODEL_SMALL="qwen/qwen3.8-27b",
        GROQ_MODEL_STRONG="openai/gpt-oss-120b",
        GROQ_STRONG_TPM_LIMIT=8000,
        GROQ_SMALL_TPM_LIMIT=6000,
    )


def _prompt_of_tokens(tokens: int) -> list[dict]:
    return [{"role": "user", "content": "x" * (tokens * CHARS_PER_TOKEN_ESTIMATE)}]


def test_groq_budget_shrinks_strong_reservation_to_fit_tpm() -> None:
    # ~4.4k input + the default 4096 reservation is the observed 8456 > 8000.
    messages = _prompt_of_tokens(4360)
    budget = groq_completion_budget(_groq_settings(), "openai/gpt-oss-120b", None, messages)
    assert budget < 4096
    assert estimate_input_tokens(messages) + budget <= 8000


def test_groq_budget_keeps_requested_when_it_already_fits() -> None:
    messages = _prompt_of_tokens(500)
    assert (
        groq_completion_budget(_groq_settings(), "openai/gpt-oss-120b", 2000, messages)
        == 2000
    )


def test_groq_budget_rejects_prompt_that_leaves_no_room_as_oversized() -> None:
    messages = _prompt_of_tokens(7500)
    with pytest.raises(ProviderError) as info:
        groq_completion_budget(_groq_settings(), "openai/gpt-oss-120b", None, messages)
    assert info.value.status_code == 413
    assert request_is_oversized(info.value)


def test_groq_budget_uses_small_model_limit() -> None:
    messages = _prompt_of_tokens(5500)
    with pytest.raises(ProviderError):
        groq_completion_budget(_groq_settings(), "qwen/qwen3.8-27b", 800, messages)
    assert groq_completion_budget(
        _groq_settings(), "openai/gpt-oss-120b", 800, messages
    ) == 800


def test_default_adapter_order_is_freellm_then_groq() -> None:
    gateway = LLMGateway(_settings())
    assert [adapter.name for adapter in gateway._adapters] == ["freellm", "groq"]


def test_freellm_uses_one_model_id_for_both_tiers() -> None:
    freellm = FakeProvider("freellm")
    gateway = LLMGateway(_settings(), primary=freellm, fallback=FakeProvider("groq"))

    gateway.chat([{"role": "user", "content": "hi"}], tier=ModelTier.SMALL)
    gateway.chat([{"role": "user", "content": "hi"}], tier=ModelTier.STRONG)

    assert [call["model"] for call in freellm.complete_calls] == ["auto", "auto"]


def test_freellm_api_base_url_always_ends_in_v1() -> None:
    assert freellm_api_base_url("http://localhost:3333/") == "http://localhost:3333/v1"
    assert freellm_api_base_url("http://localhost:3333") == "http://localhost:3333/v1"
    assert freellm_api_base_url("http://localhost:3333/v1") == "http://localhost:3333/v1"
    assert freellm_api_base_url("http://localhost:3333/v1/") == "http://localhost:3333/v1"


def _freellm_adapter(handler) -> FreeLLMAdapter:
    adapter = FreeLLMAdapter(
        _settings(FREELLM_BASE_URL="http://localhost:3333/", FREELLM_API_KEY="sk-test")
    )
    adapter._client = httpx.Client(
        base_url=freellm_api_base_url("http://localhost:3333/"),
        headers={"Authorization": "Bearer sk-test"},
        transport=httpx.MockTransport(handler),
    )
    return adapter


def test_freellm_adapter_posts_openai_payload_to_v1_chat_completions() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "some-routed-model",
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {"total_tokens": 9},
            },
        )

    adapter = _freellm_adapter(handler)
    completion = adapter.complete(
        [{"role": "user", "content": "ping"}],
        model="auto",
        tools=None,
        timeout=5,
        response_format={"type": "json_object"},
        max_tokens=256,
    )

    assert seen["url"] == "http://localhost:3333/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = json.loads(seen["body"])
    assert body["model"] == "auto"
    assert body["max_tokens"] == 256
    assert body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body
    assert completion.content == "pong"
    assert completion.tokens == 9
    assert completion.model == "some-routed-model"


def test_freellm_adapter_maps_http_errors_to_provider_error_with_status() -> None:
    adapter = _freellm_adapter(
        lambda request: httpx.Response(429, headers={"Retry-After": "3"}, json={})
    )
    with pytest.raises(ProviderError) as info:
        adapter.complete([{"role": "user", "content": "x"}], model="auto", tools=None, timeout=5)
    assert info.value.status_code == 429
    assert info.value.retry_after == 3.0


def test_freellm_adapter_error_includes_response_body_for_diagnosis() -> None:
    adapter = _freellm_adapter(
        lambda request: httpx.Response(
            502, json={"error": {"message": "All providers failed: upstream 429"}}
        )
    )
    with pytest.raises(ProviderError) as info:
        adapter.complete([{"role": "user", "content": "x"}], model="auto", tools=None, timeout=5)
    assert info.value.status_code == 502
    assert "502" in str(info.value)
    assert "All providers failed: upstream 429" in str(info.value)


def test_freellm_adapter_non_json_body_is_a_provider_error_not_a_crash() -> None:
    adapter = _freellm_adapter(
        lambda request: httpx.Response(200, text="<!doctype html><html></html>")
    )
    with pytest.raises(ProviderError):
        adapter.complete([{"role": "user", "content": "x"}], model="auto", tools=None, timeout=5)


def test_freellm_adapter_without_api_key_fails_as_provider_error() -> None:
    adapter = FreeLLMAdapter(_settings(FREELLM_API_KEY=""))
    with pytest.raises(ProviderError, match="FREELLM_API_KEY"):
        adapter.complete([{"role": "user", "content": "x"}], model="auto", tools=None, timeout=5)
