"""Layer 2 — Research Planner (step 6.5, `[SPEC AGENTS.md §28]`).

Determines what information is required from repository, website, Search
Console, optimization knowledge, and graph, given the Layer 1 objective.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.llm.gateway import ChatResult, LLMGateway, ModelTier
from app.llm.prompts import PromptBuilder
from app.planners._llm import chat_structured_with_usage
from app.planners.intent import IntentObjective

_SYSTEM = """You are the Research Planner (Layer 2) of ArchitectOS. Given a
structured objective, decide which information sources are needed before
any optimization hypothesis can be formed. Output strict JSON matching the
schema you were given, with these exact fields: repository_needed,
website_needed, search_console_needed, knowledge_needed, graph_needed,
notes.

Search Console data is not available before Phase 11 of this project;
setting search_console_needed to true only records that it would help, it
does not fetch anything."""


class ResearchPlan(BaseModel):
    """Layer 2 output `[SPEC AGENTS.md §28]`."""

    model_config = ConfigDict(extra="forbid")

    repository_needed: bool
    website_needed: bool
    search_console_needed: bool
    knowledge_needed: bool
    graph_needed: bool
    notes: str


def plan_research(gateway: LLMGateway, objective: IntentObjective) -> tuple[ResearchPlan, ChatResult]:
    builder = PromptBuilder()
    builder.set_system(_SYSTEM)
    builder.add_trusted_tool_output(
        objective.model_dump_json(),
        source="intent_planner_layer_1",
    )
    messages = builder.build_messages()
    return chat_structured_with_usage(gateway, messages, ResearchPlan, tier=ModelTier.SMALL)
