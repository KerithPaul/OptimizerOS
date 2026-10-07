"""Shared agent scaffolding (step 6.4).

Every agent runs inside an `AgentRuntime` under hard budgets (step 6.2)
and only the read-only tool set (step 6.3). `default_budget` reads its
values from `Settings` so budgets stay constructor arguments — never
hard-coded per agent, per `[SPEC AGENTS.md §51]`.
"""

from __future__ import annotations

from app.agents.runtime import AgentBudget
from app.core.config import Settings


def default_budget(settings: Settings) -> AgentBudget:
    return AgentBudget(
        max_iterations=settings.agent_max_iterations,
        max_tool_calls=settings.agent_max_tool_calls,
        max_execution_seconds=settings.agent_max_execution_seconds,
        max_token_budget=settings.agent_max_token_budget,
        max_files_modified=settings.agent_max_files_modified,
    )
