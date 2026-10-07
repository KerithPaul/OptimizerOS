"""Prioritisation (step 5.C.2, step 11.3).

`priority = impact x confidence x reach x actionability / risk` `[SPEC]`.

This is an **ArchitectOS prioritisation model, not a ranking predictor**
`[SPEC]` — it orders findings for triage, it does not forecast search
results. Every factor here is normalised to `[0, 1]` (risk excepted, see
below) so the factors are comparable across findings, and every factor
value is returned alongside the score so a reader can inspect why one
finding outranked another.

Inputs `[SPEC]`: affected page count, search impressions (when GSC data
exists), severity, implementation confidence, expected impact, regression
risk, actionability. The `reach` factor uses impressions when a finding
has GSC impressions, and falls back to affected page count when it does
not. Which input was used is recorded on the finding (`reach_input`).
The weights and mappings below are `[PROPOSED]`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.finding import Finding
from app.models.knowledge import RuleConfidence, RuleSeverity

REACH_INPUT_IMPRESSIONS = "impressions"
REACH_INPUT_PAGE_COUNT = "affected_page_count"

# [PROPOSED] severity is read as expected impact of fixing the issue.
SEVERITY_IMPACT: dict[RuleSeverity, float] = {
    RuleSeverity.LOW: 0.25,
    RuleSeverity.MEDIUM: 0.5,
    RuleSeverity.HIGH: 0.75,
    RuleSeverity.CRITICAL: 1.0,
}

# [PROPOSED] implementation confidence, from the rule's own confidence rating.
CONFIDENCE_SCORE: dict[RuleConfidence, float] = {
    RuleConfidence.LOW: 0.34,
    RuleConfidence.MEDIUM: 0.67,
    RuleConfidence.HIGH: 1.0,
}

# [PROPOSED] a recommend-only finding cannot regress anything because nothing
# has been applied; a code-change candidate carries real regression risk.
# Actionability score reuses the same mapping, inverted, for risk.
ACTIONABILITY_SCORE: dict[str, float] = {
    "recommend_only": 0.6,
    "code_change": 1.0,
    "code_or_platform_change": 1.0,
}
DEFAULT_ACTIONABILITY_SCORE = 0.5

RISK_BY_ACTIONABILITY: dict[str, float] = {
    "recommend_only": 0.15,
    "code_change": 0.6,
    "code_or_platform_change": 0.6,
}
DEFAULT_RISK = 0.4

# [PROPOSED] reach normalisation cap: an issue affecting this many pages (or
# more) is treated as maximal reach so one outlier rule can't dominate.
REACH_CAP = 50

# [PROPOSED] impressions cap for log-normalisation. 180000 (the spec's
# Search Console example) sits high on this scale without saturating it.
IMPRESSIONS_CAP = 1_000_000


@dataclass(frozen=True)
class PriorityFactors:
    impact: float
    confidence: float
    reach: float
    actionability: float
    risk: float
    reach_input: str


@dataclass(frozen=True)
class PriorityResult:
    finding_id: str
    priority: float
    factors: PriorityFactors


def reach_for_count(affected_page_count: int) -> float:
    """Normalise affected-page count to `[0, 1]` on a log scale (`[PROPOSED]`)."""

    count = max(1, affected_page_count)
    return min(1.0, math.log1p(count) / math.log1p(REACH_CAP))


def reach_for_impressions(impressions: int) -> float:
    """Normalise GSC impressions to `[0, 1]` on a log scale (`[PROPOSED]`)."""

    count = max(0, impressions)
    if count <= 0:
        return 0.0
    return min(1.0, math.log1p(count) / math.log1p(IMPRESSIONS_CAP))


def compute_priority(
    *,
    severity: RuleSeverity,
    confidence: RuleConfidence,
    affected_page_count: int,
    actionability: str,
    impressions: int | None = None,
) -> PriorityFactors:
    impact = SEVERITY_IMPACT[severity]
    confidence_score = CONFIDENCE_SCORE[confidence]
    if impressions is not None:
        reach = reach_for_impressions(impressions)
        reach_input = REACH_INPUT_IMPRESSIONS
    else:
        reach = reach_for_count(affected_page_count)
        reach_input = REACH_INPUT_PAGE_COUNT
    actionability_score = ACTIONABILITY_SCORE.get(actionability, DEFAULT_ACTIONABILITY_SCORE)
    risk = RISK_BY_ACTIONABILITY.get(actionability, DEFAULT_RISK)
    return PriorityFactors(
        impact=impact,
        confidence=confidence_score,
        reach=reach,
        actionability=actionability_score,
        risk=risk,
        reach_input=reach_input,
    )


def priority_score(factors: PriorityFactors) -> float:
    numerator = factors.impact * factors.confidence * factors.reach * factors.actionability
    return numerator / factors.risk


def _severity_of(finding: Finding) -> RuleSeverity:
    return finding.severity if isinstance(finding.severity, RuleSeverity) else RuleSeverity(finding.severity)


def _confidence_of(finding: Finding) -> RuleConfidence:
    return (
        finding.confidence
        if isinstance(finding.confidence, RuleConfidence)
        else RuleConfidence(finding.confidence)
    )


def prioritize_findings(findings: Sequence[Finding]) -> list[PriorityResult]:
    """Rank findings by priority.

    `reach` is GSC impressions when the finding has them, otherwise the
    number of findings sharing a rule (affected page count).
    """

    reach_by_rule: dict[str, int] = {}
    for finding in findings:
        reach_by_rule[finding.rule] = reach_by_rule.get(finding.rule, 0) + 1

    results: list[PriorityResult] = []
    for finding in findings:
        factors = compute_priority(
            severity=_severity_of(finding),
            confidence=_confidence_of(finding),
            affected_page_count=reach_by_rule[finding.rule],
            actionability=finding.actionability,
            impressions=finding.impressions,
        )
        results.append(
            PriorityResult(
                finding_id=finding.finding_id,
                priority=priority_score(factors),
                factors=factors,
            )
        )
    results.sort(key=lambda item: item.priority, reverse=True)
    return results


def priority_result_to_json(result: PriorityResult) -> dict[str, float | str | dict]:
    return {
        "finding_id": result.finding_id,
        "priority": round(result.priority, 4),
        "factors": {
            "impact": result.factors.impact,
            "confidence": result.factors.confidence,
            "reach": result.factors.reach,
            "actionability": result.factors.actionability,
            "risk": result.factors.risk,
            "reach_input": result.factors.reach_input,
        },
    }
