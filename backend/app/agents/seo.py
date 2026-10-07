"""SEO Agent (step 6.4, `[SPEC AGENTS.md §27]`).

Responsible for: technical SEO analysis, content SEO analysis,
interpreting SEO findings, proposing SEO plans. Must use the Evidence
Engine — every `Intervention` here is anchored to a persisted, grounded
`Finding` (`app.retrieval.evidence`), never invented by the LLM alone.
"""

from __future__ import annotations

from typing import Any

from app.agents._category import run_category_agent
from app.agents.runtime import AgentBudget, AgentRunOutcome
from app.llm.gateway import LLMGateway
from app.models.finding import Finding
from app.planners.intent import IntentObjective

CATEGORIES: tuple[str, ...] = ("technical_seo", "content_seo")


def run_seo_agent(
    *,
    findings: list[Finding],
    gateway: LLMGateway,
    budget: AgentBudget,
    objective: IntentObjective | None = None,
    research_package: dict[str, Any] | None = None,
    request_text: str | None = None,
) -> AgentRunOutcome:
    return run_category_agent(
        agent_name="seo",
        categories=CATEGORIES,
        findings=findings,
        gateway=gateway,
        budget=budget,
        objective=objective,
        research_package=research_package,
        request_text=request_text,
    )
