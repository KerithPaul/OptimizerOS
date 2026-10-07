"""Research Agent (step 6.4, `[SPEC AGENTS.md §27]`).

Responsible for: retrieving optimization knowledge, retrieving project
evidence, identifying relevant sources, resolving knowledge gaps, and
producing an evidence package. Must not modify code — it only ever calls
the read-only tools in `app.agents.tools` (step 6.3); no write tool is
reachable from this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.agents.runtime import (
    AgentBudget,
    AgentMessageRecord,
    AgentRunOutcome,
    AgentRuntime,
    AgentState,
    AgentStepResult,
)
from app.agents.tools import AgentTools, ToolResult
from app.planners.intent import IntentObjective
from app.planners.research import ResearchPlan


@dataclass
class EvidencePackage:
    """Research Agent output: what it found, and what it could not find."""

    knowledge: list[dict[str, Any]] = field(default_factory=list)
    code: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "knowledge": self.knowledge,
            "code": self.code,
            "pages": self.pages,
            "sources": self.sources,
            "gaps": self.gaps,
        }


def _steps_for(research_plan: ResearchPlan) -> list[str]:
    steps: list[str] = []
    if research_plan.knowledge_needed:
        steps.append("knowledge")
    if research_plan.repository_needed:
        steps.append("code")
    if research_plan.website_needed:
        steps.append("pages")
    if not steps:
        steps.append("knowledge")
    return steps


def _retrieval_query(objective: IntentObjective, request_text: str | None) -> str:
    """Prefer the user's words over the planner's compressed scope.

    `objective.scope` is often something generic like "all pages", which
    retrieves the same 8 knowledge items on every run. The original request
    is what actually names the topic (orphan pages, canonicals, …).
    """
    request = (request_text or "").strip()
    if request:
        return request
    return " ".join(
        part.strip() for part in (objective.objective, objective.scope) if part and part.strip()
    )


def run_research_agent(
    *,
    objective: IntentObjective,
    research_plan: ResearchPlan,
    tools: AgentTools,
    budget: AgentBudget,
    request_text: str | None = None,
) -> AgentRunOutcome:
    package = EvidencePackage()
    steps = _steps_for(research_plan)
    query = _retrieval_query(objective, request_text)

    def step(state: AgentState) -> AgentStepResult:
        index = state.iteration
        if index >= len(steps):
            return AgentStepResult(done=True, output=package.as_dict())

        kind = steps[index]
        done = index + 1 >= len(steps)

        if kind == "code" and tools.repository_id is None:
            package.gaps.append("repository not attached; code retrieval skipped")
            return AgentStepResult(done=done, output=package.as_dict())
        if kind == "pages" and tools.website_id is None:
            package.gaps.append("website not attached; page retrieval skipped")
            return AgentStepResult(done=done, output=package.as_dict())

        result = _call(tools, kind, query)
        _merge(package, kind, result)
        message = AgentMessageRecord(
            role="tool",
            tool_name=result.name,
            content=json.dumps(result.output, default=str)[:4000],
        )
        return AgentStepResult(
            done=done,
            output=package.as_dict(),
            tool_calls_made=1,
            tokens_used=result.tokens_estimate,
            messages=[message],
        )

    runtime = AgentRuntime(agent_name="research", budget=budget)
    return runtime.run(step)


def _call(tools: AgentTools, kind: str, query: str) -> ToolResult:
    if kind == "knowledge":
        return tools.retrieve_knowledge(query)
    if kind == "code":
        return tools.retrieve_code(query)
    return tools.retrieve_pages(query)


def _merge(package: EvidencePackage, kind: str, result: ToolResult) -> None:
    items = result.output.get("items", [])
    if kind == "knowledge":
        package.knowledge.extend(items)
    elif kind == "code":
        package.code.extend(items)
    else:
        package.pages.extend(items)
    package.sources.extend(str(item.get("locator")) for item in items if item.get("locator"))
    package.gaps.extend(str(gap) for gap in result.output.get("gaps", []))
