"""Three scores (step 5.C.1 verify)."""

from app.knowledge.authority import AuthorityLevel
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.retrieval.scoring import (
    MAX_SCORE,
    MIN_SCORE,
    SCORE_AI_GEO,
    SCORE_CONTENT_AEO,
    SCORE_TECHNICAL_SEO,
    compute_scores,
    compute_scores_json,
)


def _finding(**overrides) -> Finding:
    defaults = dict(
        project_id=1,
        analysis_run_id=1,
        finding_id="SEO-CANONICAL-001:deadbeef",
        observation="canonical missing",
        problem="canonical missing",
        evidence=[{"source": "https://example.com/", "excerpt": "canonical is null", "confidence": "direct"}],
        source="Google Search Central",
        source_url="https://developers.google.com/search",
        source_authority=AuthorityLevel.OFFICIAL_VENDOR_DOCS,
        rule="SEO-CANONICAL-001",
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


def test_no_findings_yields_perfect_scores() -> None:
    scores = compute_scores([])
    for score in scores.values():
        assert score.value == MAX_SCORE
        assert score.signals == ()


def test_score_decomposes_to_signal_rule_evidence_affected_resource() -> None:
    findings = [_finding()]
    scores = compute_scores(findings)
    technical = scores[SCORE_TECHNICAL_SEO]
    assert technical.value < MAX_SCORE
    assert len(technical.signals) == 1
    signal = technical.signals[0]
    assert signal.rule == "SEO-CANONICAL-001"
    assert signal.affected_resources == ("https://example.com/",)
    assert signal.evidence
    assert signal.evidence[0].finding_id == findings[0].finding_id
    assert signal.evidence[0].affected_resource == "https://example.com/"


def test_category_routes_to_the_right_score() -> None:
    geo_finding = _finding(
        finding_id="GEO-ENTITY-CLARITY-001:cafebabe",
        rule="GEO-ENTITY-CLARITY-001",
        category=RuleCategory.GEO,
    )
    scores = compute_scores([geo_finding])
    assert scores[SCORE_AI_GEO].value < MAX_SCORE
    assert scores[SCORE_TECHNICAL_SEO].value == MAX_SCORE
    assert scores[SCORE_CONTENT_AEO].value == MAX_SCORE


def test_worse_severity_deducts_more() -> None:
    low = compute_scores([_finding(severity=RuleSeverity.LOW)])[SCORE_TECHNICAL_SEO]
    critical = compute_scores([_finding(severity=RuleSeverity.CRITICAL)])[SCORE_TECHNICAL_SEO]
    assert critical.value < low.value


def test_score_json_is_serializable() -> None:
    payload = compute_scores_json([_finding()])
    assert payload[SCORE_TECHNICAL_SEO]["signals"][0]["evidence"][0]["finding_id"]


def test_single_finding_deducts_exactly_the_base_severity_amount() -> None:
    scores = compute_scores([_finding(severity=RuleSeverity.MEDIUM)])
    signal = scores[SCORE_TECHNICAL_SEO].signals[0]
    assert signal.deduction == 5
    assert scores[SCORE_TECHNICAL_SEO].value == 95


def test_many_findings_of_one_low_severity_rule_do_not_zero_the_score() -> None:
    findings = [
        _finding(
            finding_id=f"SEO-CANONICAL-001:{i:08x}",
            affected_resource=f"https://example.com/{i}",
            severity=RuleSeverity.LOW,
        )
        for i in range(55)
    ]
    scores = compute_scores(findings)
    technical = scores[SCORE_TECHNICAL_SEO]
    assert len(technical.signals) == 1
    assert technical.signals[0].finding_count == 55
    # Previously: 2 * 55 = 110 deduction, flooring the score at 0 from one
    # low-severity, single-rule signal alone.
    assert technical.value > MIN_SCORE
    assert technical.value == 90


def test_breadth_deduction_grows_slower_than_linear() -> None:
    ten = compute_scores(
        [
            _finding(finding_id=f"SEO-CANONICAL-001:{i:08x}", affected_resource=f"https://example.com/{i}")
            for i in range(10)
        ]
    )[SCORE_TECHNICAL_SEO].signals[0]
    twenty = compute_scores(
        [
            _finding(finding_id=f"SEO-CANONICAL-001:{i:08x}", affected_resource=f"https://example.com/{i}")
            for i in range(20)
        ]
    )[SCORE_TECHNICAL_SEO].signals[0]
    assert twenty.deduction > ten.deduction
    assert twenty.deduction < ten.deduction * 2
