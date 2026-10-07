"""Read-only tool set (step 6.3 verify, `[SPEC AGENTS.md §27]`).

The registry must contain no mutating tool — not disabled, absent. That
absence is the structural guarantee that Phase 6 cannot mutate a file or a
CMS resource.
"""

from __future__ import annotations

import pytest

from app.agents.tools import READ_ONLY_TOOL_NAMES, AgentTools, ToolResult
from app.intelligence.repository.graph import GraphError
from app.retrieval.hybrid import CompressedEvidence, RetrievalResult

_FORBIDDEN_NAME_FRAGMENTS = (
    "write",
    "create",
    "delete",
    "modify",
    "patch",
    "commit",
    "apply",
    "mutate",
    "update",
    "publish",
)


def _fake_result(items: list[CompressedEvidence] | None = None, gaps: list[str] | None = None) -> RetrievalResult:
    return RetrievalResult(
        query="q",
        rewritten_query="q",
        rerank="fallback",
        rerank_reason="test",
        candidates_considered=0,
        items=items or [],
        llm_messages=[],
        gaps=gaps or [],
    )


class _FakeDb:
    def __init__(self, row: object | None = None) -> None:
        self._row = row

    def scalar(self, _stmt: object) -> object | None:
        return self._row


def test_registry_contains_no_mutating_tool() -> None:
    tools = AgentTools(db=_FakeDb(), project_id=1)
    registry = tools.registry()

    assert set(registry) == READ_ONLY_TOOL_NAMES
    for name in registry:
        lowered = name.lower()
        for fragment in _FORBIDDEN_NAME_FRAGMENTS:
            assert fragment not in lowered, f"tool {name!r} looks like a write tool"


def test_retrieve_knowledge_scopes_to_the_knowledge_source_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_retrieve(query, *, scope, settings=None):
        captured["scope"] = scope
        item = CompressedEvidence(
            id="k1",
            source_type="knowledge",
            channel="semantic",
            score=1.0,
            locator="SEO-CANONICAL-001",
            summary="Canonical tags prevent duplicate content.",
            metadata={},
        )
        return _fake_result([item])

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)
    tools = AgentTools(db=_FakeDb(), project_id=1)

    result = tools.retrieve_knowledge("canonical tags")

    assert isinstance(result, ToolResult)
    assert captured["scope"].source_types == ("knowledge",)
    assert captured["scope"].project_id == 1
    assert result.output["items"][0]["locator"] == "SEO-CANONICAL-001"


def test_retrieve_code_scopes_to_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_retrieve(query, *, scope, settings=None):
        captured["scope"] = scope
        return _fake_result()

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)
    tools = AgentTools(db=_FakeDb(), project_id=1, repository_id=7)

    tools.retrieve_code("generateMetadata", file_path="app/page.tsx")

    assert captured["scope"].source_types == ("code",)
    assert captured["scope"].repository_id == 7
    assert captured["scope"].file_path == "app/page.tsx"


def test_retrieve_pages_scopes_to_the_website(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_retrieve(query, *, scope, settings=None):
        captured["scope"] = scope
        return _fake_result()

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)
    tools = AgentTools(db=_FakeDb(), project_id=1, website_id=9)

    tools.retrieve_pages("product page")

    assert captured["scope"].source_types == ("page",)
    assert captured["scope"].website_id == 9


def test_get_finding_missing_row_is_a_read_not_an_error() -> None:
    tools = AgentTools(db=_FakeDb(row=None), project_id=1)

    result = tools.get_finding("SEO-TEST-001:abc123")

    assert result.output == {"found": False, "finding_id": "SEO-TEST-001:abc123"}


def test_get_graph_neighbours_reports_a_gap_when_neo4j_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_driver():
        raise GraphError("Neo4j unavailable: stopped")

    monkeypatch.setattr("app.agents.tools.get_driver", fake_get_driver)
    tools = AgentTools(db=_FakeDb(), project_id=1)

    result = tools.get_graph_neighbours("ProductCard")

    assert result.output["neighbours"] == []
    assert "Neo4j unavailable" in result.output["gap"]
