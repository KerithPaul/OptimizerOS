"""Planner layers 1-3 (step 6.5 verify, `[SPEC AGENTS.md §28]`).

Each planner emits a Pydantic-validated structure; a malformed body is
rejected rather than coerced (reusing `app.llm.validation`, already
verified in step 2.A.3). These tests exercise the planner-specific schema
constraints and the prompt-layer wrapping around each planner's input.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from app.agents.base import default_budget
from app.core.config import Settings
from app.knowledge.authority import AuthorityLevel
from app.llm.validation import StructuredOutputError
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.llm.gateway import LLMError
from app.planners.intent import ALLOWED_ACTIONS, IntentObjective, plan_intent
from app.planners.optimization import (
    Intervention,
    can_template_intervention,
    estimate_batch_request_tokens,
    plan_optimization,
    plan_optimizations_batch,
    template_intervention,
)
from app.planners.research import ResearchPlan, plan_research


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class _FakeGateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []
        self.max_tokens: list[int | None] = []

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        self.calls.append(list(messages))
        self.max_tokens.append(max_tokens)

        class _Result:
            content = self.content
            provider = "fake"
            model = "fake-small"
            tokens = 17
            latency_ms = 5

        return _Result()


def test_intent_objective_rejects_unknown_allowed_actions() -> None:
    with pytest.raises(ValidationError):
        IntentObjective(
            objective="Improve SEO",
            scope="product pages",
            allowed_actions=["delete_database"],
            mode="audit_and_fix",
        )


def test_intent_objective_rejects_empty_allowed_actions() -> None:
    with pytest.raises(ValidationError):
        IntentObjective(objective="x", scope="y", allowed_actions=[], mode="audit_and_fix")


def test_intent_objective_accepts_the_documented_example() -> None:
    objective = IntentObjective(
        objective="SEO optimization",
        scope="product pages",
        allowed_actions=["metadata", "schema", "internal_links", "content"],
        mode="audit_and_fix",
    )
    assert set(objective.allowed_actions) <= set(ALLOWED_ACTIONS)


def test_plan_intent_wraps_the_request_and_returns_usage() -> None:
    gateway = _FakeGateway(
        '{"objective": "SEO optimization", "scope": "product pages", '
        '"allowed_actions": ["metadata"], "mode": "audit_and_fix"}'
    )

    objective, chat_result = plan_intent(gateway, "Improve SEO for product pages")  # type: ignore[arg-type]

    assert objective.objective == "SEO optimization"
    assert chat_result.tokens == 17
    from app.core.config import get_settings

    assert gateway.max_tokens == [get_settings().llm_planner_max_tokens]
    system = [m for m in gateway.calls[0] if m["role"] == "system"][0]["content"]
    assert "Improve SEO for product pages" not in system
    non_system = "\n".join(m["content"] for m in gateway.calls[0] if m["role"] != "system")
    assert "Improve SEO for product pages" in non_system


def test_plan_intent_malformed_output_raises_after_retries() -> None:
    gateway = _FakeGateway("not json")
    with pytest.raises(StructuredOutputError):
        plan_intent(gateway, "Improve SEO")  # type: ignore[arg-type]


def test_plan_intent_accepts_markdown_fenced_json() -> None:
    gateway = _FakeGateway(
        '```json\n{"objective": "SEO optimization", "scope": "product pages", '
        '"allowed_actions": ["metadata"], "mode": "audit_and_fix"}\n```'
    )

    objective, _chat = plan_intent(gateway, "Improve SEO for product pages")  # type: ignore[arg-type]

    assert objective.objective == "SEO optimization"
    assert objective.allowed_actions == ["metadata"]


def test_plan_research_wraps_the_layer_1_objective() -> None:
    gateway = _FakeGateway(
        '{"repository_needed": true, "website_needed": true, '
        '"search_console_needed": false, "knowledge_needed": true, '
        '"graph_needed": false, "notes": "need code and pages"}'
    )
    objective = IntentObjective(
        objective="SEO optimization",
        scope="product pages",
        allowed_actions=["metadata"],
        mode="audit_and_fix",
    )

    plan, chat_result = plan_research(gateway, objective)  # type: ignore[arg-type]

    assert isinstance(plan, ResearchPlan)
    assert plan.repository_needed is True
    assert chat_result.provider == "fake"
    from app.core.config import get_settings

    assert gateway.max_tokens == [get_settings().llm_planner_max_tokens]


def test_intervention_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        Intervention(
            finding_id="x",
            hypothesis="",
            intervention="y",
            expected_mechanism="z",
            risk="low",
        )


def test_plan_optimization_wraps_the_finding_as_trusted_data() -> None:
    gateway = _FakeGateway(
        '{"finding_id": "SEO-TEST-001:abc123", "hypothesis": "h", '
        '"intervention": "i", "expected_mechanism": "m", "risk": "r"}'
    )
    finding = Finding(
        id=1,
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-TEST-001:abc123",
        observation="Missing canonical tag",
        problem="Missing canonical tag",
        evidence=[{"source": "page.html", "excerpt": "no canonical tag found", "confidence": "direct"}],
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

    intervention, chat_result = plan_optimization(gateway, finding)  # type: ignore[arg-type]

    assert intervention.finding_id == "SEO-TEST-001:abc123"
    assert chat_result.model == "fake-small"
    user = "\n".join(m["content"] for m in gateway.calls[0] if m["role"] != "system")
    assert "SEO-TEST-001:abc123" in user
    assert "recommended_action" not in user
    assert "expected_mechanism" not in user
    assert "Add a canonical link tag." not in user
    assert "Canonical tags prevent duplicate-content signals." not in user
    from app.core.config import get_settings

    assert gateway.max_tokens == [get_settings().llm_planner_max_tokens]


def test_default_budget_reads_from_settings() -> None:
    settings = _settings(
        AGENT_MAX_ITERATIONS=3,
        AGENT_MAX_TOOL_CALLS=4,
        AGENT_MAX_EXECUTION_SECONDS=5,
        AGENT_MAX_TOKEN_BUDGET=6,
        AGENT_MAX_FILES_MODIFIED=0,
    )
    budget = default_budget(settings)
    assert budget.max_iterations == 3
    assert budget.max_tool_calls == 4
    assert budget.max_execution_seconds == 5
    assert budget.max_token_budget == 6
    assert budget.max_files_modified == 0


def _optimization_finding(**overrides: object) -> Finding:
    values = dict(
        id=1,
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-TEST-001:abc123",
        observation="Missing canonical tag",
        problem="Missing canonical tag",
        evidence=[{"source": "page.html", "excerpt": "no canonical tag found", "confidence": "direct"}],
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
    values.update(overrides)
    return Finding(**values)  # type: ignore[arg-type]


def test_high_confidence_finding_is_templated_from_recorded_fields() -> None:
    finding = _optimization_finding()
    assert can_template_intervention(finding) is True
    intervention = template_intervention(finding)
    assert intervention.finding_id == finding.finding_id
    assert intervention.intervention == "Add a canonical link tag."
    assert intervention.expected_mechanism == finding.expected_mechanism
    assert intervention.risk == finding.risk
    assert "guaranteed" not in intervention.hypothesis.lower()


def test_medium_confidence_finding_is_not_templated() -> None:
    finding = _optimization_finding(confidence=RuleConfidence.MEDIUM)
    assert can_template_intervention(finding) is False


def test_batch_token_estimate_includes_reserved_completion() -> None:
    from app.core.config import get_settings

    tokens = estimate_batch_request_tokens([_optimization_finding()])
    assert tokens >= get_settings().llm_planner_max_tokens


class _OversizedThenOkGateway:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        joined = "\n".join(m["content"] for m in messages if m["role"] != "system")
        ids = re.findall(r'"finding_id":\s*"([^"]+)"', joined)
        self.batch_sizes.append(len(ids))
        if len(ids) > 1:
            raise LLMError(
                "all LLM providers failed: groq attempt 1: Error code: 413 - "
                "{'error': {'message': 'Request too large for endpoint.'}}"
            )

        class _Result:
            content = json.dumps(
                {
                    "items": [
                        {
                            "finding_id": finding_id,
                            "hypothesis": "h",
                            "intervention": "i",
                            "expected_mechanism": "m",
                            "risk": "low",
                        }
                        for finding_id in ids
                    ]
                }
            )
            provider = "fake"
            model = "fake-small"
            tokens = 4
            latency_ms = 1

        return _Result()


def test_oversized_batch_is_split_and_retried() -> None:
    gateway = _OversizedThenOkGateway()
    findings = [
        _optimization_finding(id=1, finding_id="SEO-A:1"),
        _optimization_finding(id=2, finding_id="SEO-A:2"),
    ]
    items, chat_result = plan_optimizations_batch(gateway, findings)  # type: ignore[arg-type]
    assert [item.finding_id for item in items] == ["SEO-A:1", "SEO-A:2"]
    assert gateway.batch_sizes[0] == 2
    assert gateway.batch_sizes[1:] == [1, 1]
    assert chat_result.tokens == 8
