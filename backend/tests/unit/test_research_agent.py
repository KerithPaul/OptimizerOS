"""Research Agent (step 6.4 verify, `[SPEC AGENTS.md §27]`).

Retrieves knowledge/code/pages per the Research Planner's flags, resolves
a missing repository/website as an explicit gap (never a silent skip), and
never calls a write tool — `app.agents.tools.AgentTools` exposes none.
"""

from __future__ import annotations

import pytest

from app.agents.base import default_budget
from app.agents.research import run_research_agent
from app.agents.runtime import AgentRunStatus
from app.agents.tools import AgentTools
from app.core.config import Settings
from app.planners.intent import IntentObjective
from app.planners.research import ResearchPlan


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class _FakeDb:
    def scalar(self, _stmt: object) -> None:
        return None


def _objective() -> IntentObjective:
    return IntentObjective(
        objective="SEO optimization",
        scope="product pages",
        allowed_actions=["metadata"],
        mode="audit_and_fix",
    )


def _stub_retrieve(monkeypatch: pytest.MonkeyPatch, *, items=None, gaps=None) -> None:
    from app.retrieval.hybrid import RetrievalResult

    def fake_retrieve(query, *, scope, settings=None):
        return RetrievalResult(
            query=query,
            rewritten_query=query,
            rerank="fallback",
            rerank_reason="test",
            candidates_considered=0,
            items=items or [],
            llm_messages=[],
            gaps=gaps or [],
        )

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)


def test_research_agent_produces_evidence_package_from_knowledge_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_retrieve(monkeypatch)
    tools = AgentTools(db=_FakeDb(), project_id=1)
    plan = ResearchPlan(
        repository_needed=False,
        website_needed=False,
        search_console_needed=False,
        knowledge_needed=True,
        graph_needed=False,
        notes="knowledge only",
    )

    outcome = run_research_agent(
        objective=_objective(),
        research_plan=plan,
        tools=tools,
        budget=default_budget(_settings()),
    )

    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert outcome.output is not None
    assert "knowledge" in outcome.output
    assert outcome.tool_calls_used == 1


def test_research_agent_retrieves_with_the_original_request_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_retrieve(query, *, scope, settings=None):
        from app.retrieval.hybrid import RetrievalResult

        captured["query"] = query
        return RetrievalResult(
            query=query,
            rewritten_query=query,
            rerank="fallback",
            rerank_reason="test",
            candidates_considered=0,
            items=[],
            llm_messages=[],
            gaps=[],
        )

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)
    tools = AgentTools(db=_FakeDb(), project_id=1)
    plan = ResearchPlan(
        repository_needed=False,
        website_needed=False,
        search_console_needed=False,
        knowledge_needed=True,
        graph_needed=False,
        notes="knowledge only",
    )

    outcome = run_research_agent(
        objective=_objective(),
        research_plan=plan,
        tools=tools,
        budget=default_budget(_settings()),
        request_text="Improve SEO, look for the orphan pages/links",
    )

    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert captured["query"] == "Improve SEO, look for the orphan pages/links"


def test_research_agent_records_a_gap_when_repository_is_not_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_retrieve(monkeypatch)
    tools = AgentTools(db=_FakeDb(), project_id=1, repository_id=None)
    plan = ResearchPlan(
        repository_needed=True,
        website_needed=False,
        search_console_needed=False,
        knowledge_needed=False,
        graph_needed=False,
        notes="code only",
    )

    outcome = run_research_agent(
        objective=_objective(),
        research_plan=plan,
        tools=tools,
        budget=default_budget(_settings()),
    )

    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert any("repository not attached" in gap for gap in outcome.output["gaps"])
    assert outcome.tool_calls_used == 0  # the gap step never called a tool


def test_research_agent_respects_the_iteration_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_retrieve(monkeypatch)
    tools = AgentTools(db=_FakeDb(), project_id=1, repository_id=1, website_id=1)
    plan = ResearchPlan(
        repository_needed=True,
        website_needed=True,
        search_console_needed=False,
        knowledge_needed=True,
        graph_needed=False,
        notes="everything",
    )
    budget = default_budget(_settings(AGENT_MAX_ITERATIONS=2))

    outcome = run_research_agent(
        objective=_objective(),
        research_plan=plan,
        tools=tools,
        budget=budget,
    )

    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.iterations_used == 2
