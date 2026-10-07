"""Shared structured-call helper for planner layers (step 6.5).

Each planner needs both the parsed Pydantic object and the answering
call's provider/model/token accounting. `LLMGateway.chat_structured`'s
`job`/`db` accounting path writes into `jobs.llm_usage_json`, which
agent-originated calls do not use — token accounting for agents lives on
`agent_messages` (step 6.1). This re-implements the retry-and-validate
loop directly against `gateway.chat` so both values come back to the
caller instead of being discarded.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.core.config import get_settings
from app.llm.gateway import ChatResult, LLMGateway, ModelTier
from app.llm.validation import StructuredOutputError, parse_structured_with_retry

T = TypeVar("T", bound=BaseModel)


_RETRY_FEEDBACK_RAW_CHARS = 4_000


def planner_max_tokens() -> int:
    """Completion reservation for SMALL structured planners."""
    return int(get_settings().llm_planner_max_tokens)


def chat_structured_with_usage(
    gateway: LLMGateway,
    messages: list[dict[str, str]],
    schema: type[T],
    *,
    tier: ModelTier = ModelTier.SMALL,
    max_tokens: int | None = None,
) -> tuple[T, ChatResult]:
    holder: dict[str, ChatResult] = {}
    attempt_messages = [dict(message) for message in messages]
    if max_tokens is None and tier is ModelTier.SMALL:
        max_tokens = planner_max_tokens()

    def produce() -> str:
        kwargs: dict = {"tier": tier, "response_format": {"type": "json_object"}}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        result = gateway.chat(attempt_messages, **kwargs)
        holder["result"] = result
        return result.content

    def on_failure(exc: StructuredOutputError) -> None:
        attempt_messages.append(
            {"role": "assistant", "content": (exc.raw or "")[:_RETRY_FEEDBACK_RAW_CHARS]}
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

    parsed = parse_structured_with_retry(produce, schema, on_failure=on_failure)
    return parsed, holder["result"]
