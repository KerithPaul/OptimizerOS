"""Agent runtime with hard budgets (step 6.2, `[SPEC AGENTS.md §51]`).

Every agent gets five budgets: max iterations, max tool calls, max
execution time, max token budget, max files modified. Budgets are
constructor arguments on `AgentBudget` — never hard-coded per agent. On
hitting any limit the runtime **stops and reports partial progress**; it
never raises and never silently continues. Infinite agent loops are
`[FORBIDDEN]`.

`AgentRunStatus` is imported from `app.models.agent` rather than
duplicated here so a runtime outcome maps onto the `agent_runs.status`
column without translation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.models.agent import AgentRunStatus

# `agent_runs.error` is TEXT (MySQL: 65,535-byte cap); an agent step
# exception's str() has no size bound of its own.
_MAX_ERROR_CHARS = 20_000


def _truncate_error(error: str) -> str:
    if len(error) <= _MAX_ERROR_CHARS:
        return error
    return error[:_MAX_ERROR_CHARS] + f"... [truncated, {len(error)} chars total]"


@dataclass(frozen=True)
class AgentBudget:
    """Hard limits for one agent run. Always passed in, never hard-coded."""

    max_iterations: int
    max_tool_calls: int
    max_execution_seconds: float
    max_token_budget: int
    max_files_modified: int = 0  # Phase 6 agents never modify files (step 6.3).

    def __post_init__(self) -> None:
        for name in (
            "max_iterations",
            "max_tool_calls",
            "max_execution_seconds",
            "max_token_budget",
            "max_files_modified",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")


class BudgetLimit:
    ITERATIONS = "max_iterations"
    TOOL_CALLS = "max_tool_calls"
    EXECUTION_TIME = "max_execution_seconds"
    TOKEN_BUDGET = "max_token_budget"
    FILES_MODIFIED = "max_files_modified"


@dataclass(frozen=True)
class AgentMessageRecord:
    """One durable message row (persisted as `agent_messages` by the caller)."""

    role: str  # "system" | "tool" | "assistant"
    content: str
    tool_name: str | None = None
    provider: str | None = None
    model: str | None = None
    tokens: int | None = None


@dataclass
class AgentState:
    iteration: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    files_modified: int = 0
    last_output: Any = None


@dataclass
class AgentStepResult:
    """What one iteration of `step()` produced."""

    done: bool
    output: Any = None
    tool_calls_made: int = 0
    tokens_used: int = 0
    files_modified: int = 0
    messages: list[AgentMessageRecord] = field(default_factory=list)


@dataclass
class AgentRunOutcome:
    status: AgentRunStatus
    output: Any
    iterations_used: int
    tool_calls_used: int
    tokens_used: int
    files_modified: int
    stopped_reason: str | None
    messages: list[AgentMessageRecord]
    error: str | None = None


StepFn = Callable[[AgentState], AgentStepResult]


class AgentRuntime:
    """Drives one bounded agent loop. Owns no DB session and no LLM client."""

    def __init__(self, *, agent_name: str, budget: AgentBudget) -> None:
        self.agent_name = agent_name
        self.budget = budget

    def run(self, step: StepFn) -> AgentRunOutcome:
        state = AgentState()
        messages: list[AgentMessageRecord] = []
        started = time.monotonic()

        while True:
            if state.iteration >= self.budget.max_iterations:
                return self._stopped(state, messages, BudgetLimit.ITERATIONS)

            elapsed = time.monotonic() - started
            if elapsed > self.budget.max_execution_seconds:
                return self._stopped(state, messages, BudgetLimit.EXECUTION_TIME)

            try:
                result = step(state)
            except Exception as exc:  # noqa: BLE001 - an agent step failure ends this run FAILED, not the worker
                return AgentRunOutcome(
                    status=AgentRunStatus.FAILED,
                    output=None,
                    iterations_used=state.iteration,
                    tool_calls_used=state.tool_calls,
                    tokens_used=state.tokens_used,
                    files_modified=state.files_modified,
                    stopped_reason=None,
                    messages=messages,
                    error=_truncate_error(str(exc)),
                )

            state.iteration += 1
            state.tool_calls += result.tool_calls_made
            state.tokens_used += result.tokens_used
            state.files_modified += result.files_modified
            state.last_output = result.output
            messages.extend(result.messages)

            if state.tool_calls > self.budget.max_tool_calls:
                return self._stopped(state, messages, BudgetLimit.TOOL_CALLS)
            if state.tokens_used > self.budget.max_token_budget:
                return self._stopped(state, messages, BudgetLimit.TOKEN_BUDGET)
            if state.files_modified > self.budget.max_files_modified:
                return self._stopped(state, messages, BudgetLimit.FILES_MODIFIED)

            if result.done:
                return AgentRunOutcome(
                    status=AgentRunStatus.SUCCEEDED,
                    output=result.output,
                    iterations_used=state.iteration,
                    tool_calls_used=state.tool_calls,
                    tokens_used=state.tokens_used,
                    files_modified=state.files_modified,
                    stopped_reason=None,
                    messages=messages,
                )

    def _stopped(
        self,
        state: AgentState,
        messages: list[AgentMessageRecord],
        limit: str,
    ) -> AgentRunOutcome:
        return AgentRunOutcome(
            status=AgentRunStatus.PARTIAL,
            output=state.last_output,
            iterations_used=state.iteration,
            tool_calls_used=state.tool_calls,
            tokens_used=state.tokens_used,
            files_modified=state.files_modified,
            stopped_reason=limit,
            messages=messages,
        )
