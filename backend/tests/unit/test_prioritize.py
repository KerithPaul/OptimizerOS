"""Prioritisation (step 5.C.2 verify)."""

from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.retrieval.prioritize import (
    compute_priority,
    priority_result_to_json,
    priority_score,
    prioritize_findings,
    reach_for_count,
)


def _finding(**overrides) -> Finding:
    defaults = dict(
        project_id=1,
        analysis_run_id=1,
        finding_id="RULE-001:deadbeef",
        observation="obs",
        problem="obs",
        evidence=[{"source": "s", "excerpt": "e", "confidence": "direct"}],
        source="Google Search Central",
        source_url="https://developers.google.com/search",
        source_authority=AuthorityLevel.OFFICIAL_VENDOR_DOCS,
        rule="RULE-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/",
        affected_url="https://example.com/",
        affected_code_entity=None,
        expected_mechanism="mechanism",
        recommended_action="action",
        recommendation="action",
        actionability="recommend_only",
        risk="No change has been applied.",
        will_validate="re-check",
        change_worked="not_yet_applied",
        rollback="not applicable",
        status=FindingStatus.OPEN,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_reach_normalises_to_zero_one_and_saturates() -> None:
    assert 0 < reach_for_count(1) < reach_for_count(10) < reach_for_count(50) <= 1.0
    assert reach_for_count(1000) == 1.0


def test_high_impact_low_risk_outranks_low_impact_high_risk() -> None:
    high_impact_low_risk = compute_priority(
        severity=RuleSeverity.CRITICAL,
        confidence=RuleConfidence.HIGH,
        affected_page_count=10,
        actionability="recommend_only",
    )
    low_impact_high_risk = compute_priority(
        severity=RuleSeverity.LOW,
        confidence=RuleConfidence.LOW,
        affected_page_count=1,
        actionability="code_change",
    )
    assert priority_score(high_impact_low_risk) > priority_score(low_impact_high_risk)


def test_factor_values_are_inspectable() -> None:
    factors = compute_priority(
        severity=RuleSeverity.HIGH,
        confidence=RuleConfidence.MEDIUM,
        affected_page_count=5,
        actionability="code_change",
    )
    assert factors.impact == 0.75
    assert factors.confidence == 0.67
    assert 0 < factors.reach <= 1.0
    assert factors.actionability == 1.0
    assert factors.risk == 0.6


def test_prioritize_findings_ranks_and_serializes() -> None:
    critical = _finding(
        finding_id="RULE-001:aaaa", rule="RULE-001", severity=RuleSeverity.CRITICAL
    )
    low = _finding(finding_id="RULE-002:bbbb", rule="RULE-002", severity=RuleSeverity.LOW)
    ranked = prioritize_findings([low, critical])
    assert ranked[0].finding_id == critical.finding_id
    payload = priority_result_to_json(ranked[0])
    assert payload["finding_id"] == critical.finding_id
    assert set(payload["factors"]) == {
        "impact",
        "confidence",
        "reach",
        "actionability",
        "risk",
        "reach_input",
    }
    assert payload["factors"]["reach_input"] == "affected_page_count"


def test_impressions_are_used_for_reach_when_gsc_data_exists() -> None:
    from_pages = compute_priority(
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_page_count=1,
        actionability="recommend_only",
    )
    from_impressions = compute_priority(
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_page_count=1,
        actionability="recommend_only",
        impressions=180_000,
    )
    assert from_impressions.reach_input == "impressions"
    assert from_pages.reach_input == "affected_page_count"
    assert from_impressions.reach > from_pages.reach


def test_prioritize_records_impressions_input_on_finding_with_gsc() -> None:
    with_gsc = _finding(
        finding_id="RULE-001:gsc",
        rule="RULE-001",
        impressions=180_000,
        reach_input="impressions",
        affected_url="https://example.com/products/crm",
    )
    without = _finding(finding_id="RULE-002:none", rule="RULE-002")
    ranked = {item.finding_id: item for item in prioritize_findings([with_gsc, without])}
    assert ranked[with_gsc.finding_id].factors.reach_input == "impressions"
    assert ranked[without.finding_id].factors.reach_input == "affected_page_count"
