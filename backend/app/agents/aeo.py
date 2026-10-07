"""AEO Agent (step 6.4, `[SPEC AGENTS.md §27]`).

Responsible for: question detection, answer gaps, direct-answer readiness,
FAQ opportunities, answer structures, AEO change plans. Every
`Intervention` here is anchored to a persisted, grounded `Finding`.
"""

from __future__ import annotations

from typing import Any

from app.agents._category import run_category_agent
from app.agents.runtime import AgentBudget, AgentRunOutcome
from app.llm.gateway import LLMGateway
from app.models.finding import Finding
from app.planners.intent import IntentObjective

CATEGORIES: tuple[str, ...] = ("aeo",)


def run_aeo_agent(
    *,
    findings: list[Finding],
    gateway: LLMGateway,
    budget: AgentBudget,
    objective: IntentObjective | None = None,
    research_package: dict[str, Any] | None = None,
    request_text: str | None = None,
) -> AgentRunOutcome:
    return run_category_agent(
        agent_name="aeo",
        categories=CATEGORIES,
        findings=findings,
        gateway=gateway,
        budget=budget,
        objective=objective,
        research_package=research_package,
        request_text=request_text,
    )
