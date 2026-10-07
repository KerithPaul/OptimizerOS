"""Three scores (step 5.C.1).

Technical SEO Health, Content / AEO Readiness, and AI Search / GEO
Readiness. Each score decomposes `Score -> Signal -> Rule -> Evidence ->
Affected resource` — a single opaque score is `[FORBIDDEN]`.

The weights below are `[PROPOSED]`, not `[SPEC]`: the specs define
decomposability (Q4), not numbers. The chosen rubric is documented in
`knowledge/scoring-rubric.md` and labelled proposed there too.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.finding import Finding
from app.models.knowledge import RuleCategory, RuleSeverity

SCORE_TECHNICAL_SEO = "technical_seo_health"
SCORE_CONTENT_AEO = "content_aeo_readiness"
SCORE_AI_GEO = "ai_search_geo_readiness"

SCORE_LABELS: dict[str, str] = {
    SCORE_TECHNICAL_SEO: "Technical SEO Health",
    SCORE_CONTENT_AEO: "Content / AEO Readiness",
    SCORE_AI_GEO: "AI Search / GEO Readiness",
}

# [PROPOSED] which rule categories roll into which displayed score.
SCORE_CATEGORIES: dict[str, tuple[RuleCategory, ...]] = {
    SCORE_TECHNICAL_SEO: (RuleCategory.TECHNICAL_SEO,),
    SCORE_CONTENT_AEO: (RuleCategory.CONTENT_SEO, RuleCategory.AEO),
    SCORE_AI_GEO: (RuleCategory.GEO,),
}

# [PROPOSED] point deduction per open finding of a given severity. A score
# starts at 100 (no findings) and is floored at 0.
SEVERITY_DEDUCTION: dict[RuleSeverity, int] = {
    RuleSeverity.LOW: 2,
    RuleSeverity.MEDIUM: 5,
    RuleSeverity.HIGH: 10,
    RuleSeverity.CRITICAL: 18,
}

MAX_SCORE = 100
MIN_SCORE = 0

# [PROPOSED] a rule with a single open finding deducts exactly its
# `SEVERITY_DEDUCTION` amount (multiplier == 1). Each additional finding of
# the *same rule* adds a shrinking increment on a log scale instead of the
# same flat amount again, mirroring the breadth normalisation already used
# for prioritisation (`reach_for_count` in `prioritize.py`). Without this, a
# single low-severity rule that fires on dozens of pages (e.g. a template
# issue repeated site-wide) deducts linearly and floors the score at 0 on
# its own, indistinguishable from a site with many severe, independent
# problems.
def _reach_multiplier(finding_count: int) -> float:
    return 1.0 + math.log1p(max(0, finding_count - 1))


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer into one finding's evidence, for the drill-down path."""

    finding_id: str
    affected_resource: str
    excerpt: str


@dataclass(frozen=True)
class RuleSignal:
    """One rule's contribution to a score: `Signal -> Rule -> Evidence -> Affected resource`."""

    rule: str
    severity: RuleSeverity
    deduction: int
    finding_count: int
    affected_resources: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class Score:
    key: str
    label: str
    value: int
    max_value: int = MAX_SCORE
    signals: tuple[RuleSignal, ...] = field(default_factory=tuple)


def _category_of(finding: Finding) -> RuleCategory:
    return finding.category if isinstance(finding.category, RuleCategory) else RuleCategory(finding.category)


def _severity_of(finding: Finding) -> RuleSeverity:
    return finding.severity if isinstance(finding.severity, RuleSeverity) else RuleSeverity(finding.severity)


def _evidence_refs(finding: Finding) -> tuple[EvidenceRef, ...]:
    rows = finding.evidence or []
    return tuple(
        EvidenceRef(
            finding_id=finding.finding_id,
            affected_resource=finding.affected_resource,
            excerpt=str(row.get("excerpt", "")) if isinstance(row, dict) else str(row),
        )
        for row in rows
    )


def _score_for_category(
    key: str,
    categories: tuple[RuleCategory, ...],
    findings: Sequence[Finding],
) -> Score:
    relevant = [f for f in findings if _category_of(f) in categories]

    by_rule: dict[str, list[Finding]] = {}
    for finding in relevant:
        by_rule.setdefault(finding.rule, []).append(finding)

    signals: list[RuleSignal] = []
    total_deduction = 0
    for rule, rows in sorted(by_rule.items()):
        severity = max((_severity_of(r) for r in rows), key=_severity_rank)
        per_finding_deduction = SEVERITY_DEDUCTION[severity]
        deduction = round(per_finding_deduction * _reach_multiplier(len(rows)))
        total_deduction += deduction
        evidence: list[EvidenceRef] = []
        for row in rows:
            evidence.extend(_evidence_refs(row))
        signals.append(
            RuleSignal(
                rule=rule,
                severity=severity,
                deduction=deduction,
                finding_count=len(rows),
                affected_resources=tuple(r.affected_resource for r in rows),
                evidence=tuple(evidence),
            )
        )

    value = max(MIN_SCORE, min(MAX_SCORE, MAX_SCORE - total_deduction))
    return Score(key=key, label=SCORE_LABELS[key], value=value, signals=tuple(signals))


def _severity_rank(severity: RuleSeverity) -> int:
    order = (RuleSeverity.LOW, RuleSeverity.MEDIUM, RuleSeverity.HIGH, RuleSeverity.CRITICAL)
    return order.index(severity)


def compute_scores(findings: Sequence[Finding]) -> dict[str, Score]:
    """One `Score` per displayed score key, each decomposable to evidence rows."""

    return {
        key: _score_for_category(key, categories, findings)
        for key, categories in SCORE_CATEGORIES.items()
    }


def score_to_json(score: Score) -> dict[str, Any]:
    return {
        "key": score.key,
        "label": score.label,
        "value": score.value,
        "max_value": score.max_value,
        "signals": [
            {
                "rule": signal.rule,
                "severity": signal.severity.value,
                "deduction": signal.deduction,
                "finding_count": signal.finding_count,
                "affected_resources": list(signal.affected_resources),
                "evidence": [
                    {
                        "finding_id": item.finding_id,
                        "affected_resource": item.affected_resource,
                        "excerpt": item.excerpt,
                    }
                    for item in signal.evidence
                ],
            }
            for signal in score.signals
        ],
    }


def compute_scores_json(findings: Sequence[Finding]) -> dict[str, Any]:
    return {key: score_to_json(score) for key, score in compute_scores(findings).items()}
