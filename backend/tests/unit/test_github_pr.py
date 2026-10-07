"""PR body is one logical unit linking Change Set, findings, evidence, validation."""

from datetime import datetime, timezone

from app.connectors.github.mcp import get_ci_status
from app.connectors.github.pull_request import build_pr_body, pr_title
from app.connectors.github.rest import GitHubApiError
from app.knowledge.authority import AuthorityLevel
from app.models.change import (
    ChangeSet,
    ChangeSetStatus,
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationResult,
    ValidationRun,
    ValidationRunStatus,
)
from app.models.finding import Finding, FindingStatus
from app.models.github import CiStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity


class _FakeRest:
    def __init__(self, combined, checks=None, error=None):
        self._combined = combined
        self._checks = checks or {"check_runs": []}
        self._error = error

    def combined_status(self, owner, repo, ref):
        if self._error:
            raise GitHubApiError(self._error)
        return self._combined

    def check_runs(self, owner, repo, ref):
        return self._checks


def _change_set() -> ChangeSet:
    return ChangeSet(
        id=42,
        project_id=1,
        objective="Improve product-page SEO",
        description="metadata + canonical + schema",
        finding_ids_json=["SEO-CANONICAL-001:abc", "SEO-TITLE-001:def"],
        evidence_json=[
            {"source": "page", "excerpt": "17 product URLs share the same title.", "confidence": "direct"}
        ],
        affected_resources_json=["app/meta.ts", "app/schema.ts"],
        risk="low",
        status=ChangeSetStatus.APPLIED,
        applied_at=datetime.now(timezone.utc),
    )


def _finding() -> Finding:
    return Finding(
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-CANONICAL-001:abc",
        observation="Canonical tag is missing",
        problem="Canonical tag is missing",
        evidence=[{"source": "page", "excerpt": "no <link rel=canonical>", "confidence": "direct"}],
        source="ArchitectOS rule catalog",
        source_url="https://example.com/rules/seo-canonical-001",
        source_authority=AuthorityLevel.OFFICIAL_STANDARD,
        rule="SEO-CANONICAL-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_resource="app/meta.ts",
        expected_mechanism="page-specific canonical",
        recommended_action="add canonical",
        recommendation="add canonical",
        actionability="code_change",
        risk="low",
        will_validate="canonical present",
        change_worked="unknown",
        rollback="snapshot",
        status=FindingStatus.VALIDATED,
    )


def test_pr_body_links_change_set_findings_evidence_validation() -> None:
    change_set = _change_set()
    run = ValidationRun(
        id=9,
        project_id=1,
        status=ValidationRunStatus.PASSED,
    )
    results = [
        ValidationResult(
            validation_run_id=9,
            check_type=ValidationCheckType.SEO,
            status=ValidationCheckStatus.PASSED,
            detail="canonical present",
        )
    ]
    body = build_pr_body(
        change_set,
        findings=[_finding()],
        validation_run=run,
        validation_results=results,
        commit_sha="abc123",
        branch="architectos/42-improve-product-page-seo",
    )
    assert "Change Set #42" in body
    assert "SEO-CANONICAL-001:abc" in body
    assert "no <link rel=canonical>" in body
    assert "17 product URLs share the same title." in body
    assert "validation run #9" in body
    assert "canonical present" in body
    assert "one logical unit" in body.lower()
    assert "not one PR per file" in body
    assert pr_title(change_set).startswith("ArchitectOS Change Set #42:")


def test_ci_maps_check_failure_explicitly() -> None:
    rest = _FakeRest(
        {"state": "success"},
        {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "failure"}]},
    )
    result = get_ci_status(rest, "acme", "shop", "abc123")
    assert result["status"] == CiStatus.FAILURE.value


def test_ci_unavailable_is_not_success() -> None:
    rest = _FakeRest({}, error="GitHub API 401: bad credentials")
    rest.check_runs = lambda *a, **k: (_ for _ in ()).throw(GitHubApiError("GitHub API 401"))
    result = get_ci_status(rest, "acme", "shop", "abc123")
    assert result["status"] == CiStatus.UNAVAILABLE.value
    assert result["status"] != CiStatus.SUCCESS.value
