"""Reviewer Agent (step 7.8 verify, `[SPEC AGENTS.md §27]`).

The deterministic override must win even when the LLM itself would
approve: any failed validation check, or a file outside the Change
Plan's target_files, forces `approved=False`.
"""

from __future__ import annotations

import json

from app.agents.base import default_budget
from app.agents.reviewer import run_reviewer_agent
from app.agents.runtime import AgentRunStatus
from app.core.config import Settings
from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.planners.change import ChangePlan


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class _FakeGateway:
    def __init__(self, approved: bool) -> None:
        self.approved = approved

    def chat(self, messages, *, tier=None, response_format=None):
        payload = {
            "finding_id": "SEO-CANONICAL-001:abc123",
            "approved": self.approved,
            "reasons": ["looks correct"] if self.approved else ["diff is risky"],
            "regressions_detected": [],
        }

        class _Result:
            content = json.dumps(payload)
            provider = "fake"
            model = "fake-strong"
            tokens = 12
            latency_ms = 5

        return _Result()


def _finding() -> Finding:
    return Finding(
        id=1,
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-CANONICAL-001:abc123",
        observation="Missing canonical tag",
        problem="Missing canonical tag",
        evidence=[{"source": "page.html", "excerpt": "no canonical tag", "confidence": "direct"}],
        source="ArchitectOS rule catalog",
        source_url="https://example.com/rules/seo-canonical-001",
        source_authority=AuthorityLevel.OFFICIAL_STANDARD,
        rule="SEO-CANONICAL-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.HIGH,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/product",
        affected_code_entity="app/products/[id]/page.tsx",
        expected_mechanism="Canonical tags prevent duplicate-content signals.",
        recommended_action="Add a canonical link tag.",
        recommendation="Add a canonical link tag.",
        actionability="code_change",
        risk="Low risk; metadata-only change.",
        will_validate="Re-crawl and confirm the canonical tag is present.",
        change_worked="not_yet_applied",
        rollback="Remove the added tag.",
        status=FindingStatus.OPEN,
    )


def _change_plan() -> ChangePlan:
    return ChangePlan(
        finding_id="SEO-CANONICAL-001:abc123",
        target_files=["app/products/[id]/page.tsx"],
        target_symbols=["generateMetadata"],
        reuse_notes="reuse generateMetadata",
        expected_diff_summary="add canonical tag",
        required_tests=[],
        required_validation=["build"],
    )


def test_clean_patch_with_passing_validation_is_approved() -> None:
    gateway = _FakeGateway(approved=True)
    outcome, verdict = run_reviewer_agent(
        finding=_finding(),
        change_plan=_change_plan(),
        diff_summary="added a canonical link tag",
        actual_files=["app/products/[id]/page.tsx"],
        validation_summary=[{"check_type": "build", "status": "passed", "detail": None}],
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert outcome.status is AgentRunStatus.SUCCEEDED
    assert verdict is not None
    assert verdict.approved is True


def test_failed_validation_forces_rejection_even_if_llm_approves() -> None:
    gateway = _FakeGateway(approved=True)
    outcome, verdict = run_reviewer_agent(
        finding=_finding(),
        change_plan=_change_plan(),
        diff_summary="added a canonical link tag",
        actual_files=["app/products/[id]/page.tsx"],
        validation_summary=[{"check_type": "unit_test", "status": "failed", "detail": "1 test failed"}],
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert verdict is not None
    assert verdict.approved is False
    assert any("validation check failed" in r for r in verdict.reasons)


def test_file_outside_change_plan_forces_rejection_even_if_llm_approves() -> None:
    gateway = _FakeGateway(approved=True)
    outcome, verdict = run_reviewer_agent(
        finding=_finding(),
        change_plan=_change_plan(),
        diff_summary="touched an unrelated auth file",
        actual_files=["app/products/[id]/page.tsx", "auth/login.ts"],
        validation_summary=[{"check_type": "build", "status": "passed", "detail": None}],
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert verdict is not None
    assert verdict.approved is False
    assert any("auth/login.ts" in r for r in verdict.reasons)


def test_skipped_validation_on_dry_run_does_not_force_rejection() -> None:
    gateway = _FakeGateway(approved=True)
    outcome, verdict = run_reviewer_agent(
        finding=_finding(),
        change_plan=_change_plan(),
        diff_summary="added a canonical link tag",
        actual_files=["app/products/[id]/page.tsx"],
        validation_summary=[],
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        dry_run=True,
    )
    assert verdict is not None
    assert verdict.approved is True
    assert outcome.status is AgentRunStatus.SUCCEEDED


def test_not_applicable_validation_does_not_force_rejection() -> None:
    gateway = _FakeGateway(approved=True)
    outcome, verdict = run_reviewer_agent(
        finding=_finding(),
        change_plan=_change_plan(),
        diff_summary="file: app/products/[id]/page.tsx\nchange_summary: add canonical",
        actual_files=["app/products/[id]/page.tsx"],
        validation_summary=[
            {"check_type": "build", "status": "passed", "detail": None},
            {"check_type": "unit_test", "status": "not_applicable", "detail": 'no "test" script'},
        ],
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
        diff_files=[
            {
                "file_path": "app/products/[id]/page.tsx",
                "change_summary": "add canonical",
                "unified_diff": "+canonical",
            }
        ],
    )
    assert verdict is not None
    assert verdict.approved is True
    assert outcome.status is AgentRunStatus.SUCCEEDED


def test_llm_rejection_is_respected() -> None:
    gateway = _FakeGateway(approved=False)
    outcome, verdict = run_reviewer_agent(
        finding=_finding(),
        change_plan=_change_plan(),
        diff_summary="risky change",
        actual_files=["app/products/[id]/page.tsx"],
        validation_summary=[{"check_type": "build", "status": "passed", "detail": None}],
        gateway=gateway,  # type: ignore[arg-type]
        budget=default_budget(_settings()),
    )
    assert verdict is not None
    assert verdict.approved is False
