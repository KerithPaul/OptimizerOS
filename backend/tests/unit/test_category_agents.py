"""SEO / AEO / GEO agents (step 6.4 verify, `[SPEC AGENTS.md §27]`).

Each agent filters `Finding` rows to its own categories, ranks them by
priority (step 5.C.2), and turns each into an `Intervention` via the
Layer 3 Optimization Planner — never inventing one without a grounded
Finding behind it.
"""

from __future__ import annotations

import json
import re

from app.agents.aeo import run_aeo_agent
from app.agents.base import default_budget
from app.agents.geo import run_geo_agent
from app.agents.runtime import AgentRunStatus
from app.agents.seo import run_seo_agent
from app.core.config import Settings
from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.planners.intent import IntentObjective


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


_FINDING_ID_RE = re.compile(r'"finding_id":\s*"([^"]+)"')


class _FakeGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.max_tokens: list[int | None] = []

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        self.calls += 1
        self.max_tokens.append(max_tokens)
        joined = "\n".join(m["content"] for m in messages if m["role"] != "system")
        finding_ids = _FINDING_ID_RE.findall(joined) or ["unknown"]

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
                        for finding_id in finding_ids
                    ]
                }
            )
            provider = "fake"
            model = "fake-small"
            tokens = 10

        return _Result()


def _finding(
    finding_id: str,
    category: RuleCategory,
    severity: RuleSeverity,
    *,
    confidence: RuleConfidence = RuleConfidence.HIGH,
    affected_resource: str = "https://example.com/p",
    recommended_action: str = "a",
    status: FindingStatus = FindingStatus.OPEN,
) -> Finding:
    return Finding(
        id=hash(finding_id) % 10_000,
        project_id=1,
        analysis_run_id=1,
        finding_id=finding_id,
        observation="obs",
        problem="obs",
        evidence=[{"source": "s", "excerpt": "e", "confidence": "direct"}],
        source="ArchitectOS rule catalog",
        source_url="https://example.com/rules/x",
        source_authority=AuthorityLevel.OFFICIAL_STANDARD,
        rule=finding_id.split(":")[0],
        rule_version=1,
        category=category,
        severity=severity,
        confidence=confidence,
        affected_resource=affected_resource,
        expected_mechanism="m",
        recommended_action=recommended_action,
        recommendation=recommended_action,
        actionability="recommend_only",
        risk="low",
        will_validate="v",
        change_worked="not_yet_applied",
        rollback="r",
        status=status,
    )


def _findings() -> list[Finding]:
    return [
        _finding("SEO-A:1", RuleCategory.TECHNICAL_SEO, RuleSeverity.HIGH),
        _finding("SEO-B:2", RuleCategory.CONTENT_SEO, RuleSeverity.LOW),
        _finding("AEO-A:3", RuleCategory.AEO, RuleSeverity.MEDIUM),
        _finding("GEO-A:4", RuleCategory.GEO, RuleSeverity.CRITICAL),
    ]


def test_seo_agent_only_touches_seo_categories() -> None:
    outcome = run_seo_agent(
        findings=_findings(),
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"SEO-A:1", "SEO-B:2"}


def test_aeo_agent_only_touches_aeo_findings() -> None:
    outcome = run_aeo_agent(
        findings=_findings(),
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"AEO-A:3"}


def test_geo_agent_only_touches_geo_findings_and_never_claims_guarantees() -> None:
    outcome = run_geo_agent(
        findings=_findings(),
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"GEO-A:4"}
    for item in outcome.output:
        for phrase in ("guaranteed", "will rank #1", "guaranteed citation"):
            assert phrase not in item["hypothesis"].lower()
            assert phrase not in item["intervention"].lower()


def test_seo_agent_respects_the_iteration_budget() -> None:
    # Medium confidence so Layer 3 still calls the LLM. More SEO findings
    # than one batch holds, so the budget still has something to cut off
    # after the first batched iteration.
    findings = [
        _finding(
            f"SEO-A:{i}",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            confidence=RuleConfidence.MEDIUM,
        )
        for i in range(6)
    ]
    budget = default_budget(_settings(AGENT_MAX_ITERATIONS=1))
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=budget,
    )
    assert outcome.status is AgentRunStatus.PARTIAL
    assert outcome.iterations_used == 1


def _objective(scope: str = "product pages") -> IntentObjective:
    return IntentObjective(
        objective="SEO optimization",
        scope=scope,
        allowed_actions=["metadata"],
        mode="audit_and_fix",
    )


def test_high_confidence_findings_are_templated_without_an_llm_call() -> None:
    gateway = _FakeGateway()
    outcome = run_seo_agent(
        findings=_findings(),
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert gateway.calls == 0
    assert outcome.tokens_used == 0
    seo = {item["finding_id"]: item for item in outcome.output}
    assert seo["SEO-A:1"]["intervention"] == "a"
    assert seo["SEO-B:2"]["intervention"] == "a"


def test_medium_confidence_findings_still_call_the_optimizer() -> None:
    gateway = _FakeGateway()
    findings = [
        _finding(
            "SEO-M:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            confidence=RuleConfidence.MEDIUM,
        )
    ]
    outcome = run_seo_agent(
        findings=findings,
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert gateway.calls == 1
    from app.core.config import get_settings

    assert gateway.max_tokens == [get_settings().llm_planner_max_tokens]
    assert outcome.output[0]["finding_id"] == "SEO-M:1"


def test_seo_agent_keeps_top_n_findings() -> None:
    findings = [
        _finding(
            f"SEO-A:{i}",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            confidence=RuleConfidence.MEDIUM,
        )
        for i in range(12)
    ]
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert len(outcome.output) == 8


def test_seo_agent_filters_by_objective_scope() -> None:
    findings = [
        _finding(
            "SEO-P:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            affected_resource="https://example.com/product/a",
        ),
        _finding(
            "SEO-A:2",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.CRITICAL,
            affected_resource="https://example.com/about",
        ),
    ]
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        objective=_objective("product pages"),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"SEO-P:1"}


def test_seo_agent_filters_by_research_package_locators() -> None:
    findings = [
        _finding(
            "SEO-P:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            affected_resource="https://example.com/product/a",
        ),
        _finding(
            "SEO-A:2",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.CRITICAL,
            affected_resource="https://example.com/about",
        ),
    ]
    package = {
        "knowledge": [],
        "code": [],
        "pages": [{"locator": "https://example.com/product/a", "summary": "product"}],
        "sources": ["https://example.com/product/a"],
        "gaps": [],
    }
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        research_package=package,
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"SEO-P:1"}


def test_scope_miss_does_not_empty_the_run() -> None:
    findings = [
        _finding(
            "SEO-A:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            affected_resource="https://example.com/about",
        )
    ]
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        objective=_objective("product pages"),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert outcome.output[0]["finding_id"] == "SEO-A:1"


def test_seo_agent_prefers_request_topic_over_higher_priority_unrelated_findings() -> None:
    findings = [
        _finding(
            "SEO-IMAGE-DIMENSIONS-001:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.CRITICAL,
            affected_resource="https://example.com/_next/image?url=logo",
        ),
        _finding(
            "SEO-ORPHAN-PAGE-001:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.MEDIUM,
            affected_resource="https://example.com/",
        ),
    ]
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        objective=_objective("all of the pages"),
        request_text="Improve SEO for all of the pages, look for the orphan pages/links",
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"SEO-ORPHAN-PAGE-001:1"}


def test_homepage_research_url_does_not_select_every_path() -> None:
    findings = [
        _finding(
            "SEO-IMAGE-DIMENSIONS-001:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.CRITICAL,
            affected_resource="https://example.com/_next/image?url=logo",
        ),
        _finding(
            "SEO-ORPHAN-PAGE-001:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.MEDIUM,
            affected_resource="https://example.com/",
        ),
    ]
    package = {
        "knowledge": [],
        "code": [],
        "pages": [{"locator": "https://example.com/"}],
        "sources": ["https://example.com/", "sro/lib/cms/defaults/publications.ts:1"],
        "gaps": [],
    }
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        research_package=package,
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"SEO-ORPHAN-PAGE-001:1"}


def test_research_rule_locator_matches_findings_on_other_urls() -> None:
    findings = [
        _finding(
            "SEO-IMAGE-DIMENSIONS-001:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.CRITICAL,
            affected_resource="https://example.com/img",
        ),
        _finding(
            "SEO-ORPHAN-PAGE-001:1",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.LOW,
            affected_resource="https://example.com/hidden",
        ),
    ]
    package = {
        "knowledge": [
            {
                "locator": "SEO-ORPHAN-PAGE-001",
                "metadata": {"rule_id": "SEO-ORPHAN-PAGE-001"},
            }
        ],
        "code": [],
        "pages": [],
        "sources": ["SEO-ORPHAN-PAGE-001"],
        "gaps": [],
    }
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        research_package=package,
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    ids = {item["finding_id"] for item in outcome.output}
    assert ids == {"SEO-ORPHAN-PAGE-001:1"}


def test_seo_agent_skips_findings_that_are_already_handled() -> None:
    findings = [
        _finding("SEO-OPEN:1", RuleCategory.TECHNICAL_SEO, RuleSeverity.HIGH),
        _finding(
            "SEO-FIXED:2",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.HIGH,
            status=FindingStatus.FIXED,
        ),
        _finding(
            "SEO-VALIDATED:3",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.MEDIUM,
            status=FindingStatus.VALIDATED,
        ),
        _finding(
            "SEO-REJECTED:4",
            RuleCategory.TECHNICAL_SEO,
            RuleSeverity.LOW,
            status=FindingStatus.REJECTED,
        ),
    ]
    outcome = run_seo_agent(
        findings=findings,
        gateway=_FakeGateway(),  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert isinstance(outcome.output, dict)
    ids = {item["finding_id"] for item in outcome.output["interventions"]}
    assert ids == {"SEO-OPEN:1", "SEO-REJECTED:4"}
    omitted = {item["finding_id"]: item["status"] for item in outcome.output["omitted_findings"]}
    assert omitted == {"SEO-FIXED:2": "FIXED", "SEO-VALIDATED:3": "VALIDATED"}
    assert outcome.output["omitted_count"] == 2
