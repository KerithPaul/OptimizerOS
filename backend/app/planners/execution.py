"""Layer 5 — Execution Planner (step 7.1, `[SPEC AGENTS.md §28]`).

`change plan -> modification order, dependency constraints, platform
operations, sandbox operations`. Fully deterministic: the Layer 4
`ChangePlan` already named the files (most-likely-to-change first) and
the sandbox checks in `required_validation`. Sequencing those is not an
LLM task `[SPEC "Deterministic code before LLM reasoning"]`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from app.llm.gateway import ChatResult
from app.planners.change import ChangePlan

# Same set the previous LLM prompt allowed. Only values that already
# appear in ChangePlan.required_validation are copied through.
_SANDBOX_OPERATIONS = frozenset({"install", "build", "lint", "typecheck", "unit_test"})


class ExecutionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int
    action: str
    detail: str

    @field_validator("action", "detail")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def _string_as_singleton_list(value: object) -> object:
    """Small models often emit a list[str] field as one English sentence.

    That is still one constraint/command, not a different schema. Wrap it
    so validation can stay `list[str]` for callers. Other types are left
    unchanged and fail the strict list check as usual.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return [] if not stripped else [stripped]
    return value


class ExecutionPlan(BaseModel):
    """Layer 5 output `[SPEC AGENTS.md §28]`."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    steps: list[ExecutionStep]
    dependencies: list[str]
    sandbox_operations: list[str]

    @field_validator("dependencies", "sandbox_operations", mode="before")
    @classmethod
    def _wrap_string_list_fields(cls, value: object) -> object:
        return _string_as_singleton_list(value)

    @field_validator("steps")
    @classmethod
    def _at_least_one_step(cls, value: list[ExecutionStep]) -> list[ExecutionStep]:
        if not value:
            raise ValueError("an Execution Plan must have at least one step")
        return value


def sandbox_operations_from_validation(required_validation: list[str]) -> list[str]:
    """Copy known sandbox ops out of Layer 4 `required_validation`, first-seen order."""
    operations: list[str] = []
    seen: set[str] = set()
    for item in required_validation:
        key = item.strip().lower()
        if key in _SANDBOX_OPERATIONS and key not in seen:
            operations.append(key)
            seen.add(key)
    return operations


def plan_execution(change_plan: ChangePlan) -> tuple[ExecutionPlan, ChatResult]:
    """Sequence `target_files` in listed order; map validation checks to sandbox ops."""
    plan = ExecutionPlan(
        finding_id=change_plan.finding_id,
        steps=[
            ExecutionStep(order=index, action="write_file", detail=path)
            for index, path in enumerate(change_plan.target_files, start=1)
        ],
        dependencies=[],
        sandbox_operations=sandbox_operations_from_validation(
            change_plan.required_validation
        ),
    )
    return plan, ChatResult(
        content=plan.model_dump_json(),
        provider="deterministic",
        model="",
        tokens=0,
        latency_ms=0,
    )
