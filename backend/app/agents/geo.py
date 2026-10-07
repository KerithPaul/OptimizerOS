"""GEO Agent (step 6.4, `[SPEC AGENTS.md §27]`).

Responsible for: AI-search readiness, entity clarity, factual structure,
source attribution, citation readiness, content accessibility,
answerability. Never claims guaranteed AI-search ranking/citation results
— enforced in the Layer 3 Optimization Planner's system prompt
(`app.planners.optimization`), which every `Intervention` here goes
through.
"""

from __future__ import annotations

from typing import Any

from app.agents._category import run_category_agent
from app.agents.runtime import AgentBudget, AgentRunOutcome
from app.llm.gateway import LLMGateway
from app.models.finding import Finding
from app.planners.intent import IntentObjective

CATEGORIES: tuple[str, ...] = ("geo",)


def run_geo_agent(
    *,
    findings: list[Finding],
    gateway: LLMGateway,
    budget: AgentBudget,
    objective: IntentObjective | None = None,
    research_package: dict[str, Any] | None = None,
    request_text: str | None = None,
) -> AgentRunOutcome:
    return run_category_agent(
        agent_name="geo",
        categories=CATEGORIES,
        findings=findings,
        gateway=gateway,
        budget=budget,
        objective=objective,
        research_package=research_package,
        request_text=request_text,
    )
