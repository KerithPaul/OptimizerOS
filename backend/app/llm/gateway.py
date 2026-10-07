"""LLMGateway (step 2.A.1) and per-call accounting (step 2.A.4).

FreeLLMAPI (self-hosted, OpenAI-compatible) is primary; Groq is the fallback.
OpenAI is a future provider and is not constructed.
No other ArchitectOS module may import a provider SDK.

Capabilities: chat, structured output, tool calling, streaming, model
selection, token accounting, retry, timeouts, fallback. Model IDs come
from env. Small models for classification/extraction/summarisation;
strong models for architecture reasoning, code modification, and review.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.llm.validation import StructuredOutputError, parse_structured_with_retry
from app.models.job import Job

logger = logging.getLogger("architectos.llm.gateway")

T = TypeVar("T", bound=BaseModel)

# Status codes that mean "the provider is throttling us" rather than
# "the request itself is broken" — only these ever earn a backoff delay
# before the next same-adapter retry.
_RATE_LIMIT_STATUS_CODES = frozenset({429, 503})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 20.0
# Groq: "Limit 6000, Requested 12788". Either order shows up in the wild.
_LIMIT_THEN_REQUESTED = re.compile(
    r"limit\s+(\d+)\s*,\s*requested\s+(\d+)", re.IGNORECASE
)
_REQUESTED_THEN_LIMIT = re.compile(
    r"requested\s+(\d+)\s*,\s*limit\s+(\d+)", re.IGNORECASE
)

# JSON/code-heavy prompts run ~3 chars/token; 4 undercounts them and is how
# a "4000-token" request arrived at Groq as 12788.
CHARS_PER_TOKEN_ESTIMATE = 3
# Slack for the estimate being off, and for the few tokens Groq adds per message.
_GROQ_TPM_SAFETY_TOKENS = 200
# Below this a reasoning model has no room to think AND emit its JSON, so
# sending the request to Groq just buys a truncated or empty answer.
_GROQ_MIN_COMPLETION_TOKENS = 1024


class LLMError(Exception):
    """Every configured provider failed. Must not be reported as success."""


class ProviderError(Exception):
    """A single provider failed. The gateway may retry or fall back."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def cap_groq_max_tokens(
    settings: Settings, model: str, max_tokens: int | None
) -> int:
    """Completion budget sent to Groq for `model`.

    Groq reserves OTPM from `max_tokens` before generating. A SMALL
    on-demand model whose OTPM is 1000 rejects any reservation above
    that limit (observed: Limit 1000, Requested 1088). STRONG is left
    unchanged: we do not have a measured OTPM for it.
    """
    requested = int(settings.llm_max_tokens if max_tokens is None else max_tokens)
    small = (settings.groq_model_small or "").strip()
    limit = int(settings.groq_small_otpm_limit)
    if small and model == small and requested > limit:
        logger.warning(
            "clamping groq small-model max_tokens from %s to OTPM limit %s (model=%s)",
            requested,
            limit,
            model,
        )
        return limit
    return requested


def estimate_input_tokens(
    messages: Sequence[dict], tools: list[dict] | None = None
) -> int:
    """Conservative input-token estimate for a chat request."""
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif content:
            chars += len(json.dumps(content, default=str))
    if tools:
        chars += len(json.dumps(tools, default=str))
    return chars // CHARS_PER_TOKEN_ESTIMATE + 4 * len(messages)


def groq_completion_budget(
    settings: Settings,
    model: str,
    max_tokens: int | None,
    messages: Sequence[dict],
    tools: list[dict] | None = None,
) -> int:
    """`max_tokens` to send to Groq so that input + reservation fits the TPM window.

    Groq charges `input tokens + max_tokens` against tokens-per-minute
    *before* generating, and rejects the call outright when that sum
    exceeds the per-request limit (observed on gpt-oss-120b: "Limit 8000,
    Requested 8456" from ~4.4k of input plus the default 4096 reservation).
    Shrinking the reservation keeps requests that would otherwise be
    refused. When even the minimum useful completion cannot fit, raise a
    non-retryable 413 so the gateway falls through to the next provider
    instead of burning a call that is certain to be rejected.
    """
    requested = cap_groq_max_tokens(settings, model, max_tokens)
    small = (settings.groq_model_small or "").strip()
    limit = int(
        settings.groq_small_tpm_limit
        if small and model == small
        else settings.groq_strong_tpm_limit
    )
    prompt = estimate_input_tokens(messages, tools)
    room = limit - prompt - _GROQ_TPM_SAFETY_TOKENS
    if room >= requested:
        return requested
    if room < min(_GROQ_MIN_COMPLETION_TOKENS, requested):
        raise ProviderError(
            f"Request too large for model `{model}` on tokens per minute (TPM): "
            f"Limit {limit}, Requested {prompt + requested}",
            status_code=413,
        )
    logger.warning(
        "clamping groq max_tokens from %s to %s to fit TPM limit %s "
        "(model=%s, est_input=%s)",
        requested,
        room,
        limit,
        model,
        prompt,
    )
    return room


def request_is_oversized(exc: BaseException) -> bool:
    """True when resending the same request cannot succeed.

    A 413 / "request too large" is a payload-size rejection. A TPM 429
    whose `Requested` exceeds `Limit` is the same class of failure: the
    single call is larger than the quota window, so waiting and retrying
    it unchanged will fail again. Generic RPM 429s are not oversized —
    those should wait, not shrink.
    """
    status = getattr(exc, "status_code", None)
    if status == 413:
        return True
    text = str(exc)
    lowered = text.lower()
    if "request too large" in lowered or "payload too large" in lowered:
        return True
    if "error code: 413" in lowered or "status=413" in lowered:
        return True
    match = _LIMIT_THEN_REQUESTED.search(text)
    if match and int(match.group(2)) > int(match.group(1)):
        return True
    match = _REQUESTED_THEN_LIMIT.search(text)
    if match and int(match.group(1)) > int(match.group(2)):
        return True
    return False


def _retry_signal(exc: Exception) -> tuple[int | None, float | None]:
    """Best-effort status code / Retry-After extraction from a provider SDK error.

    httpx and groq both surface the underlying HTTP response as
    `.response` (or `.raw_response`) on their exceptions. Absent that shape,
    both values are None and the caller retries immediately, unchanged from
    before this existed.
    """
    response = getattr(exc, "response", None) or getattr(exc, "raw_response", None)
    if response is None:
        return None, None
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", None)
    retry_after: float | None = None
    raw = headers.get("Retry-After") if headers is not None and hasattr(headers, "get") else None
    if raw is not None:
        try:
            retry_after = float(raw)
        except (TypeError, ValueError):
            retry_after = None
    return status_code, retry_after


def _with_response_detail(exc: Exception, limit: int = 500) -> str:
    """`str(exc)` plus the HTTP response body, if any.

    httpx's status error text omits the body, which is where an
    OpenAI-compatible router (FreeLLMAPI) says why an upstream failed.
    """
    message = str(exc)
    response = getattr(exc, "response", None)
    if response is None:
        return message
    try:
        detail = response.text.strip()
    except Exception:
        # A streamed response that was never read has no `.text` yet.
        return message
    if not detail:
        return message
    return f"{message} | response: {detail[:limit]}"


class ModelTier(str, Enum):
    SMALL = "small"
    STRONG = "strong"


class LLMCallRecord(BaseModel):
    """One LLM call's accounting fields (step 2.A.4)."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


def record_llm_call(db: Session, job: Job, call: LLMCallRecord) -> None:
    """Append one call's accounting to `jobs.llm_usage_json` and commit."""
    usage = list(job.llm_usage_json or [])
    usage.append(call.model_dump())
    job.llm_usage_json = usage
    db.commit()
    logger.info(
        "llm call (job_id=%s, provider=%s, model=%s, tokens=%s, latency_ms=%s)",
        job.id,
        call.provider,
        call.model,
        call.tokens,
        call.latency_ms,
    )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatResult:
    content: str
    provider: str
    model: str
    tokens: int
    latency_ms: int
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class _Completion:
    content: str
    tokens: int
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ProviderAdapter(Protocol):
    name: str

    def complete(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        tools: list[dict] | None,
        timeout: float,
        response_format: dict | None = None,
        max_tokens: int | None = None,
    ) -> _Completion: ...

    def stream(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        timeout: float,
    ) -> Iterator[str]: ...


class LLMGateway:
    """Provider-agnostic LLM entrypoint. The rest of ArchitectOS talks only to this."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        primary: ProviderAdapter | None = None,
        fallback: ProviderAdapter | None = None,
        providers: list[ProviderAdapter] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if providers is not None:
            self._adapters: list[ProviderAdapter] = list(providers)
        elif primary is not None or fallback is not None:
            self._adapters = [a for a in (primary, fallback) if a is not None]
        else:
            self._adapters = [
                FreeLLMAdapter(self._settings),
                GroqAdapter(self._settings),
            ]

    def chat(
        self,
        messages: Sequence[dict],
        *,
        tier: ModelTier = ModelTier.SMALL,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        max_tokens: int | None = None,
        job: Job | None = None,
        db: Session | None = None,
    ) -> ChatResult:
        result = self._complete_with_fallback(
            messages,
            tier=tier,
            tools=tools,
            response_format=response_format,
            max_tokens=max_tokens,
        )
        if job is not None and db is not None:
            record_llm_call(
                db,
                job,
                LLMCallRecord(
                    provider=result.provider,
                    model=result.model,
                    tokens=result.tokens,
                    latency_ms=result.latency_ms,
                ),
            )
        return result

    def chat_structured(
        self,
        messages: Sequence[dict],
        schema: type[T],
        *,
        tier: ModelTier = ModelTier.SMALL,
        job: Job | None = None,
        db: Session | None = None,
    ) -> T:
        attempt_messages = [dict(message) for message in messages]

        def produce() -> str:
            return self.chat(
                attempt_messages,
                tier=tier,
                job=job,
                db=db,
                response_format={"type": "json_object"},
            ).content

        def on_failure(exc: StructuredOutputError) -> None:
            attempt_messages.append(
                {"role": "assistant", "content": (exc.raw or "")[:4000]}
            )
            attempt_messages.append(
                {
                    "role": "user",
                    "content": (
                        "The previous response is not valid against the required JSON schema. "
                        f"{exc}. Reply with a JSON object only, no commentary."
                    ),
                }
            )

        return parse_structured_with_retry(
            produce,
            schema,
            max_retries=self._settings.llm_max_retries,
            on_failure=on_failure,
        )

    def chat_stream(
        self,
        messages: Sequence[dict],
        *,
        tier: ModelTier = ModelTier.SMALL,
    ) -> Iterator[str]:
        errors: list[str] = []
        for adapter in self._adapters:
            model = self._model_id(adapter.name, tier)
            try:
                yield from adapter.stream(
                    messages,
                    model=model,
                    timeout=float(self._settings.llm_request_timeout_seconds),
                )
                return
            except ProviderError as exc:
                errors.append(f"{adapter.name}: {exc}")
                logger.warning("llm stream provider failed (provider=%s): %s", adapter.name, exc)
        raise LLMError("all LLM providers failed: " + "; ".join(errors))

    def _complete_with_fallback(
        self,
        messages: Sequence[dict],
        *,
        tier: ModelTier,
        tools: list[dict] | None,
        response_format: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        attempts_per_provider = self._settings.llm_max_retries + 1
        errors: list[str] = []
        timeout = float(self._settings.llm_request_timeout_seconds)

        for adapter in self._adapters:
            model = self._model_id(adapter.name, tier)
            if not model:
                errors.append(f"{adapter.name}: model id is not configured")
                continue
            for attempt in range(1, attempts_per_provider + 1):
                started = time.perf_counter()
                try:
                    raw = adapter.complete(
                        messages,
                        model=model,
                        tools=tools,
                        timeout=timeout,
                        response_format=response_format,
                        max_tokens=max_tokens,
                    )
                    if not (raw.content or "").strip() and not raw.tool_calls:
                        raise ProviderError("provider returned empty content")
                except ProviderError as exc:
                    errors.append(f"{adapter.name} attempt {attempt}: {exc}")
                    logger.warning(
                        "llm provider failed (provider=%s, attempt=%s): %s",
                        adapter.name,
                        attempt,
                        exc,
                    )
                    if not self._should_retry(exc):
                        logger.warning(
                            "llm request rejected by %s (status=%s); not retrying "
                            "the same request, moving to next provider",
                            adapter.name,
                            exc.status_code,
                        )
                        break
                    if attempt < attempts_per_provider:
                        self._await_retry(adapter.name, exc, attempt)
                    continue
                latency_ms = int((time.perf_counter() - started) * 1000)
                logger.info(
                    "llm answered (provider=%s, model=%s, tokens=%s, latency_ms=%s)",
                    adapter.name,
                    raw.model,
                    raw.tokens,
                    latency_ms,
                )
                return ChatResult(
                    content=raw.content,
                    provider=adapter.name,
                    model=raw.model,
                    tokens=raw.tokens,
                    latency_ms=latency_ms,
                    tool_calls=raw.tool_calls,
                )
        raise LLMError("all LLM providers failed: " + "; ".join(errors))

    def _await_retry(self, provider: str, exc: ProviderError, attempt: int) -> None:
        delay = self._retry_delay_seconds(exc, attempt)
        if delay <= 0:
            return
        logger.info(
            "llm rate limited, backing off before retry (provider=%s, attempt=%s, delay_s=%.2f)",
            provider,
            attempt,
            delay,
        )
        time.sleep(delay)

    def _should_retry(self, exc: ProviderError) -> bool:
        """Whether the same request to the same adapter is worth retrying.

        Oversized payloads (413, or TPM 429 where Requested > Limit) cannot
        succeed on retry. A missing status (timeout, connection error) or a
        remaining-quota 429/503 can succeed once the transient condition
        clears. Any other 4xx is a deterministic rejection of this exact
        request.
        """
        if request_is_oversized(exc):
            return False
        return self._is_retryable_status(exc.status_code)

    @staticmethod
    def _is_retryable_status(status_code: int | None) -> bool:
        if status_code is None or status_code in _RATE_LIMIT_STATUS_CODES:
            return True
        return not (400 <= status_code < 500)

    @staticmethod
    def _retry_delay_seconds(exc: ProviderError, attempt: int) -> float:
        """Only back off on an actual rate-limit signal; other failures retry at once."""
        if exc.retry_after is not None:
            return max(0.0, min(exc.retry_after, _BACKOFF_CAP_SECONDS))
        if exc.status_code in _RATE_LIMIT_STATUS_CODES:
            base = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            jitter = random.uniform(0, base * 0.25)
            return min(base + jitter, _BACKOFF_CAP_SECONDS)
        return 0.0

    def _model_id(self, provider: str, tier: ModelTier) -> str:
        if provider == "freellm":
            # FreeLLMAPI picks the backing model itself (`auto`), for both tiers.
            return self._settings.freellm_model
        if provider == "groq":
            return (
                self._settings.groq_model_small
                if tier is ModelTier.SMALL
                else self._settings.groq_model_strong
            )
        return ""


def freellm_api_base_url(configured: str) -> str:
    """OpenAI-compatible root for FreeLLMAPI.

    The server only answers under `/v1`; any other path serves its web UI
    (HTML), so `http://localhost:3333/` is normalised to `.../v1`.
    """
    base = configured.strip().rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


class FreeLLMAdapter:
    """OpenAI-compatible adapter for a self-hosted FreeLLMAPI instance."""

    name = "freellm"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._api_key = settings.freellm_api_key
        self._timeout = float(settings.llm_request_timeout_seconds)
        self._client: httpx.Client | None = None
        if self._api_key:
            self._client = httpx.Client(
                base_url=freellm_api_base_url(settings.freellm_base_url),
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )

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
        client = self._require_client()
        payload: dict = {
            "model": model,
            "messages": list(messages),
            "max_tokens": int(
                self._settings.llm_max_tokens if max_tokens is None else max_tokens
            ),
        }
        if tools:
            payload["tools"] = tools
        if response_format:
            payload["response_format"] = response_format
        try:
            resp = client.post("/chat/completions", json=payload, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            status_code, retry_after = _retry_signal(exc)
            raise ProviderError(
                _with_response_detail(exc), status_code=status_code, retry_after=retry_after
            ) from exc
        return _from_openai_json(body, model)

    def stream(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        timeout: float,
    ) -> Iterator[str]:
        client = self._require_client()
        payload = {"model": model, "messages": list(messages), "stream": True}
        try:
            with client.stream("POST", "/chat/completions", json=payload, timeout=timeout) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[len("data: ") :]
                    if data.strip() == "[DONE]":
                        break
                    chunk = json.loads(data)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    def _require_client(self) -> httpx.Client:
        if self._client is None:
            raise ProviderError("FREELLM_API_KEY is not set")
        return self._client


class GroqAdapter:
    name = "groq"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._api_key = settings.groq_api_key
        self._timeout = float(settings.llm_request_timeout_seconds)
        self._client = None
        if self._api_key:
            from groq import Groq

            self._client = Groq(api_key=self._api_key, timeout=self._timeout)

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
        client = self._require_client()
        kwargs: dict = {
            "model": model,
            "messages": list(messages),
            "timeout": timeout,
            "max_tokens": groq_completion_budget(
                self._settings, model, max_tokens, messages, tools
            ),
        }
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            status_code, retry_after = _retry_signal(exc)
            raise ProviderError(str(exc), status_code=status_code, retry_after=retry_after) from exc
        return _from_openai_shaped(resp, model)

    def stream(
        self,
        messages: Sequence[dict],
        *,
        model: str,
        timeout: float,
    ) -> Iterator[str]:
        client = self._require_client()
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=list(messages),
                timeout=timeout,
                stream=True,
            )
            for event in stream:
                choices = getattr(event, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    yield content
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    def _require_client(self):
        if self._client is None:
            raise ProviderError("GROQ_API_KEY is not set")
        return self._client


def _normalize_content(content: object) -> str:
    """Coerce OpenAI-shaped `content` (string, null, or parts list) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if text is not None:
                    parts.append(str(text))
        return "".join(parts)
    return str(content)


def _assistant_text(message: object) -> str:
    """Visible assistant text, falling back to reasoning when content is empty.

    GLM-5.3 (and other reasoning models) often put the only usable text in
    `reasoning_content` / `reasoning`, with `content` left null.
    """
    if isinstance(message, dict):
        content = _normalize_content(message.get("content"))
        if content.strip():
            return content
        for key in ("reasoning_content", "reasoning"):
            alt = _normalize_content(message.get(key))
            if alt.strip():
                return alt
        return content
    content = _normalize_content(getattr(message, "content", None))
    if content.strip():
        return content
    for attr in ("reasoning_content", "reasoning"):
        alt = _normalize_content(getattr(message, attr, None))
        if alt.strip():
            return alt
    return content


def _from_openai_json(data: dict, fallback_model: str) -> _Completion:
    """Parse a raw OpenAI-shaped JSON response body (dict, not SDK object)."""
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("provider returned no choices")
    message = choices[0].get("message")
    if message is None:
        raise ProviderError("provider returned no message")
    content = _assistant_text(message)
    tool_calls: list[ToolCall] = []
    for item in message.get("tool_calls") or []:
        function = item.get("function")
        if not function:
            continue
        tool_calls.append(
            ToolCall(
                id=str(item.get("id", "")),
                name=str(function.get("name", "")),
                arguments=str(function.get("arguments") or ""),
            )
        )
    usage = data.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    model = str(data.get("model") or fallback_model)
    return _Completion(content=content, tokens=tokens, model=model, tool_calls=tool_calls)


def _from_openai_shaped(resp: object, fallback_model: str) -> _Completion:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise ProviderError("provider returned no choices")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise ProviderError("provider returned no message")
    content = _assistant_text(message)
    tool_calls: list[ToolCall] = []
    for item in getattr(message, "tool_calls", None) or []:
        function = getattr(item, "function", None)
        if function is None:
            continue
        tool_calls.append(
            ToolCall(
                id=str(getattr(item, "id", "")),
                name=str(getattr(function, "name", "")),
                arguments=str(getattr(function, "arguments", "") or ""),
            )
        )
    usage = getattr(resp, "usage", None)
    tokens = int(getattr(usage, "total_tokens", 0) or 0) if usage is not None else 0
    model = str(getattr(resp, "model", None) or fallback_model)
    return _Completion(content=content, tokens=tokens, model=model, tool_calls=tool_calls)
