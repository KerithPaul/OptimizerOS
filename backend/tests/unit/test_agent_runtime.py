"""Agent runtime budgets (step 6.2 verify, `[SPEC AGENTS.md §51]`).

Every budget must STOP the run and report partial progress rather than
raising or looping forever; an internal step failure ends the run FAILED
without crashing the caller.
"""

from __future__ import annotations

import pytest

from app.agents.runtime import (
    AgentBudget,
    AgentRunOutcome,
    AgentRunStatus,
    AgentRuntime,
    AgentState,
    AgentStepResult,
    BudgetLimit,
)


def _budget(**overrides: object) -> AgentBudget:
    values: dict[str, object] = {
        "max_iterations": 10,
        "max_tool_calls": 10,
        "max_execution_seconds": 60.0,
        "max_token_budget": 10_000,
        "max_files_modified": 0,
    }
    values.update(overrides)
    return AgentBudget(**values)  # type: ignore[arg-type]


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValueError):
        AgentBudget(
            max_iterations=-1,
            max_tool_calls=1,
            max_execution_seconds=1,
            max_token_budget=1,
        )


def test_two_iteration_budget_stops_at_two_and_reports_partial() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget(max_iterations=2))
    calls = {"n": 0}

    def step(state: AgentState) -> AgentStepResult:
        calls["n"] += 1
        return AgentStepResult(done=False, output=calls["n"])

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.stopped_reason == BudgetLimit.ITERATIONS
    assert outcome.iterations_used == 2
    assert calls["n"] == 2  # stopped before a third step, never errored, never looped forever


def test_successful_completion_reports_succeeded_with_output() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget())
    plan = [False, False, True]

    def step(state: AgentState) -> AgentStepResult:
        done = plan[state.iteration]
        return AgentStepResult(done=done, output=state.iteration)

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert outcome.output == 2
    assert outcome.iterations_used == 3
    assert outcome.stopped_reason is None


def test_tool_call_budget_stops_and_reports_partial() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget(max_tool_calls=1))

    def step(state: AgentState) -> AgentStepResult:
        return AgentStepResult(done=False, output=None, tool_calls_made=1)

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.stopped_reason == BudgetLimit.TOOL_CALLS
    assert outcome.tool_calls_used == 2


def test_token_budget_stops_and_reports_partial() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget(max_token_budget=100))

    def step(state: AgentState) -> AgentStepResult:
        return AgentStepResult(done=False, output=None, tokens_used=60)

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.stopped_reason == BudgetLimit.TOKEN_BUDGET
    assert outcome.tokens_used == 120


def test_files_modified_budget_defaults_to_zero_and_stops_on_any_modification() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget())

    def step(state: AgentState) -> AgentStepResult:
        return AgentStepResult(done=False, output=None, files_modified=1)

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.stopped_reason == BudgetLimit.FILES_MODIFIED


def test_execution_time_budget_stops_and_reports_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 0.0, 50.0])
    monkeypatch.setattr("app.agents.runtime.time.monotonic", lambda: next(clock))
    runtime = AgentRuntime(agent_name="t", budget=_budget(max_execution_seconds=10.0))
    calls = {"n": 0}

    def step(state: AgentState) -> AgentStepResult:
        calls["n"] += 1
        return AgentStepResult(done=False, output=None)

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.stopped_reason == BudgetLimit.EXECUTION_TIME
    assert calls["n"] == 1


def test_partial_stop_still_carries_the_last_known_output() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget(max_iterations=2))

    def step(state: AgentState) -> AgentStepResult:
        return AgentStepResult(done=False, output={"partial": state.iteration})

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.output == {"partial": 1}


def test_step_exception_ends_the_run_failed_without_raising() -> None:
    runtime = AgentRuntime(agent_name="t", budget=_budget())

    def step(state: AgentState) -> AgentStepResult:
        raise RuntimeError("boom")

    outcome = runtime.run(step)

    assert isinstance(outcome, AgentRunOutcome)
    assert outcome.status is AgentRunStatus.FAILED
    assert outcome.output is None
    assert outcome.error == "boom"


def test_step_exception_truncates_an_oversized_message() -> None:
    """`agent_runs.error` is TEXT (MySQL: 65,535-byte cap); an exception's
    str() has no size bound of its own."""
    from app.agents.runtime import _MAX_ERROR_CHARS

    runtime = AgentRuntime(agent_name="t", budget=_budget())
    huge = "x" * (_MAX_ERROR_CHARS + 5000)

    def step(state: AgentState) -> AgentStepResult:
        raise RuntimeError(huge)

    outcome = runtime.run(step)

    assert outcome.status is AgentRunStatus.FAILED
    assert len(outcome.error) < len(huge)
    assert str(len(huge)) in outcome.error


def test_messages_accumulate_across_iterations() -> None:
    from app.agents.runtime import AgentMessageRecord

    runtime = AgentRuntime(agent_name="t", budget=_budget())
    plan = [False, True]

    def step(state: AgentState) -> AgentStepResult:
        done = plan[state.iteration]
        return AgentStepResult(
            done=done,
            output=None,
            messages=[AgentMessageRecord(role="tool", content=f"msg-{state.iteration}")],
        )

    outcome = runtime.run(step)

    assert [m.content for m in outcome.messages] == ["msg-0", "msg-1"]
