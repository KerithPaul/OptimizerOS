"""Read-only tool set (step 6.3, `[SPEC AGENTS.md §27]`).

retrieve knowledge · retrieve code · retrieve pages · get finding · get
graph neighbours. **No write tool exists in this module.** Not disabled —
absent. That absence is the structural guarantee that Phase 6 cannot
mutate a file or a CMS resource: an agent can only ever call a tool that
is in `AgentTools.registry()`, and every one of them reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.intelligence.repository.graph import GraphError, get_driver
from app.models.finding import Finding
from app.retrieval.hybrid import RetrievalError, RetrievalScope, retrieve

# Rough retrieval-side token estimate ([PROPOSED]: no LLM call happens in a
# tool call, so there is no real token count — 4 characters/token is the
# same heuristic budget systems commonly use for a non-tokenizer estimate).
_CHARS_PER_TOKEN_ESTIMATE = 4

READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "retrieve_knowledge",
        "retrieve_code",
        "retrieve_pages",
        "get_finding",
        "get_graph_neighbours",
    }
)


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: dict[str, Any]
    tokens_estimate: int = 0


def _value(field_value: object) -> str:
    return field_value.value if hasattr(field_value, "value") else str(field_value)


@dataclass
class AgentTools:
    """Bound to one project. Every method is a read; none accepts content to write."""

    db: Session
    project_id: int
    repository_id: int | None = None
    website_id: int | None = None
    settings: Settings = field(default_factory=get_settings)

    def registry(self) -> dict[str, Any]:
        return {
            "retrieve_knowledge": self.retrieve_knowledge,
            "retrieve_code": self.retrieve_code,
            "retrieve_pages": self.retrieve_pages,
            "get_finding": self.get_finding,
            "get_graph_neighbours": self.get_graph_neighbours,
        }

    def retrieve_knowledge(self, query: str, *, rule_id: str | None = None) -> ToolResult:
        scope = RetrievalScope(
            project_id=self.project_id,
            source_types=("knowledge",),
            rule_id=rule_id,
        )
        return self._retrieve("retrieve_knowledge", query, scope)

    def retrieve_code(self, query: str, *, file_path: str | None = None) -> ToolResult:
        scope = RetrievalScope(
            project_id=self.project_id,
            repository_id=self.repository_id,
            source_types=("code",),
            file_path=file_path,
        )
        return self._retrieve("retrieve_code", query, scope)

    def retrieve_pages(self, query: str, *, url: str | None = None) -> ToolResult:
        scope = RetrievalScope(
            project_id=self.project_id,
            website_id=self.website_id,
            source_types=("page",),
            url=url,
        )
        return self._retrieve("retrieve_pages", query, scope)

    def get_finding(self, finding_id: str) -> ToolResult:
        row = self.db.scalar(
            select(Finding).where(
                Finding.project_id == self.project_id,
                Finding.finding_id == finding_id,
            )
        )
        if row is None:
            return ToolResult(
                name="get_finding",
                output={"found": False, "finding_id": finding_id},
            )
        payload = {
            "found": True,
            "finding_id": row.finding_id,
            "observation": row.observation,
            "rule": row.rule,
            "category": _value(row.category),
            "severity": _value(row.severity),
            "confidence": _value(row.confidence),
            "affected_resource": row.affected_resource,
            "status": _value(row.status),
        }
        return ToolResult(
            name="get_finding",
            output=payload,
            tokens_estimate=len(row.observation) // _CHARS_PER_TOKEN_ESTIMATE,
        )

    def get_graph_neighbours(self, name: str) -> ToolResult:
        try:
            driver = get_driver()
        except GraphError as exc:
            return ToolResult(
                name="get_graph_neighbours",
                output={"neighbours": [], "gap": str(exc)},
            )
        try:
            with driver.session() as session:
                records = session.run(
                    """
                    MATCH (n)-[rel]-(m)
                    WHERE n.project_id = $project_id
                      AND (
                            n.name = $name
                            OR n.stable_id = $name
                            OR n.file_path = $name
                            OR n.url = $name
                          )
                    RETURN m.name AS name, m.kind AS kind, m.file_path AS file_path,
                           m.url AS url, type(rel) AS relation
                    LIMIT 50
                    """,
                    project_id=self.project_id,
                    name=name,
                )
                rows = [dict(record) for record in records]
        except GraphError as exc:
            return ToolResult(
                name="get_graph_neighbours",
                output={"neighbours": [], "gap": str(exc)},
            )
        except Exception as exc:
            return ToolResult(
                name="get_graph_neighbours",
                output={"neighbours": [], "gap": f"Neo4j unavailable: {exc}"},
            )
        text_len = sum(len(str(row.get("name") or "")) for row in rows)
        return ToolResult(
            name="get_graph_neighbours",
            output={"neighbours": rows},
            tokens_estimate=text_len // _CHARS_PER_TOKEN_ESTIMATE,
        )

    def _retrieve(self, name: str, query: str, scope: RetrievalScope) -> ToolResult:
        try:
            result = retrieve(query, scope=scope, settings=self.settings)
        except RetrievalError as exc:
            return ToolResult(name=name, output={"items": [], "gaps": [str(exc)]})
        items = [
            {
                "id": item.id,
                "source_type": item.source_type,
                "channel": item.channel,
                "score": item.score,
                "locator": item.locator,
                "summary": item.summary,
                "metadata": item.metadata,
            }
            for item in result.items
        ]
        chars = sum(len(item["summary"]) for item in items)
        return ToolResult(
            name=name,
            output={"items": items, "gaps": result.gaps, "rewritten_query": result.rewritten_query},
            tokens_estimate=chars // _CHARS_PER_TOKEN_ESTIMATE,
        )
