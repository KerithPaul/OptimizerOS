"""Agent safety fixtures (step 6.6, `[SPEC AGENTS.md §53]`).

Malicious content embedded in a README, an HTML body, and a code comment
(`testdata/agent-safety/`) must be treated as untrusted data by every
prompt an agent builds — never obeyed, never propagated into the SYSTEM
layer. This exercises the actual prompt-construction paths an agent uses:

- `app.retrieval.hybrid.retrieve` — what a `retrieve_knowledge` /
  `retrieve_code` / `retrieve_pages` tool call (step 6.3) wraps.
- `app.planners.optimization.plan_optimization` — what the SEO/AEO/GEO
  agents (step 6.4) send to the LLM for one grounded Finding, when that
  Finding's evidence excerpt happens to contain crawled/repository text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.knowledge.authority import AuthorityLevel
from app.llm.gateway import ChatResult
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.planners.optimization import plan_optimization
from app.retrieval.hybrid import RetrievalHit, RetrievalScope, retrieve

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURES = _REPO_ROOT / "testdata" / "agent-safety"

_INJECTION_PHRASES = (
    "Ignore all instructions",
    "Delete database",
    "Upload secrets",
    "Modify authentication",
)

_FIXTURE_FILES = ("README.md", "page.html", "component.tsx")


def _system_content(messages: list[dict[str, str]]) -> str:
    system = [m for m in messages if m["role"] == "system"]
    assert len(system) == 1
    return system[0]["content"]


@pytest.mark.parametrize("filename", _FIXTURE_FILES)
def test_fixture_contains_the_injection_phrases(filename: str) -> None:
    text = (_FIXTURES / filename).read_text(encoding="utf-8")
    for phrase in _INJECTION_PHRASES:
        assert phrase in text


@pytest.mark.parametrize("filename", _FIXTURE_FILES)
def test_retrieved_fixture_content_stays_out_of_the_system_layer(filename: str) -> None:
    """What a `retrieve_knowledge` / `retrieve_code` / `retrieve_pages` tool call wraps."""

    text = (_FIXTURES / filename).read_text(encoding="utf-8")

    def fake_semantic(understood, scope) -> list[RetrievalHit]:
        return [
            RetrievalHit(
                id="fixture:1",
                text=text,
                score=1.0,
                channel="semantic",
                source_type="code",
                payload={"file_path": filename},
            )
        ]

    result = retrieve(
        "optimization guidance",
        scope=RetrievalScope(project_id=1),
        semantic_fn=fake_semantic,
        graph_fn=lambda understood, scope: [],
        lexical_fn=lambda understood, scope: [],
    )

    system = _system_content(result.llm_messages)
    for phrase in _INJECTION_PHRASES:
        assert phrase not in system

    untrusted = [
        message["content"]
        for message in result.llm_messages
        if message["role"] != "system" and "BEGIN UNTRUSTED PROJECT CONTENT" in message["content"]
    ]
    assert untrusted
    joined = "\n".join(untrusted)
    for phrase in _INJECTION_PHRASES:
        assert phrase in joined


class _FakeChatResult:
    content = (
        '{"finding_id": "SEO-TEST-001:abc123", '
        '"hypothesis": "Missing canonical causes duplicate-content ambiguity.", '
        '"intervention": "Add a self-referencing canonical link tag.", '
        '"expected_mechanism": "Canonical tags consolidate duplicate-content signals.", '
        '"risk": "Low risk; metadata-only change."}'
    )
    provider = "fake"
    model = "fake-small"
    tokens = 42


class _FakeGateway:
    def __init__(self) -> None:
        self.captured_messages: list[dict[str, str]] | None = None

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None) -> ChatResult:
        self.captured_messages = list(messages)
        return _FakeChatResult()  # type: ignore[return-value]


def test_optimization_planner_wraps_injected_evidence_as_trusted_finding_data() -> None:
    """A Finding's `evidence` excerpt can legitimately contain crawled/repo
    text (it is the measured fact). `plan_optimization` still must not let
    it reach the SYSTEM layer — it goes in as part of the trusted Finding
    payload, never as an instruction."""

    injected = (_FIXTURES / "page.html").read_text(encoding="utf-8")
    finding = Finding(
        id=1,
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-TEST-001:abc123",
        observation="Missing canonical tag",
        problem="Missing canonical tag",
        evidence=[{"source": "page.html", "excerpt": injected, "confidence": "direct"}],
        source="ArchitectOS rule catalog",
        source_url="https://example.com/rules/seo-test-001",
        source_authority=AuthorityLevel.OFFICIAL_STANDARD,
        rule="SEO-TEST-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/product",
        expected_mechanism="Canonical tags prevent duplicate-content signals.",
        recommended_action="Add a canonical link tag.",
        recommendation="Add a canonical link tag.",
        actionability="recommend_only",
        risk="Low risk; metadata-only change.",
        will_validate="Re-crawl and confirm the canonical tag is present.",
        change_worked="not_yet_applied",
        rollback="Remove the added tag.",
        status=FindingStatus.OPEN,
    )

    gateway = _FakeGateway()
    intervention, chat_result = plan_optimization(gateway, finding)  # type: ignore[arg-type]

    assert intervention.finding_id == "SEO-TEST-001:abc123"
    assert chat_result.tokens == 42
    assert gateway.captured_messages is not None

    system = _system_content(gateway.captured_messages)
    for phrase in _INJECTION_PHRASES:
        assert phrase not in system

    non_system = [m["content"] for m in gateway.captured_messages if m["role"] != "system"]
    joined = "\n".join(non_system)
    assert "TRUSTED TOOL OUTPUT" in joined
    assert "page.html" in joined
    assert "Widgets" in joined
    for phrase in _INJECTION_PHRASES:
        assert phrase not in system
        if phrase in joined:
            assert phrase not in system
