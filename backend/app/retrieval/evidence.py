"""Evidence Engine (step 5.B.2).

Assembles a Finding from rule hits + retrieved context + measured page/code
facts. An LLM may later phrase observation and mechanism; it is never the
origin of the underlying fact.

Evidence row shape (ClearSite harvest, Appendix D): source, excerpt,
selector, value, confidence (`direct` / `derived` / `heuristic`).

A finding whose evidence list is empty cannot be constructed or persisted.
Every finding answers the nine questions in IMPLEMENTATION_PLAN_V2.md §1.2.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy.orm import Session

from app.knowledge.authority import AuthorityLevel
from app.knowledge.evaluator import RuleHit, UNINGESTED_RULE_VERSION
from app.knowledge.rulefile import KNOWLEDGE_ROOT, RuleFile, load_all_rule_files
from app.models.finding import AnalysisRun, Finding, FindingStatus
from app.models.knowledge import (
    OptimizationRule,
    RuleCategory,
    RuleConfidence,
    RuleSeverity,
)
from app.retrieval.hybrid import CompressedEvidence

logger = logging.getLogger("architectos.retrieval.evidence")

CHANGE_WORKED_NOT_APPLIED = "not_yet_applied"
ROLLBACK_NOT_APPLICABLE = (
    "No change has been applied. Rollback is not applicable until a Change Transaction exists."
)

NINE_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("observation", "WHAT IS WRONG?"),
    ("affected_resource", "WHERE IS IT?"),
    ("evidence", "WHAT EVIDENCE PROVES IT?"),
    ("rule", "WHICH RULE/KNOWLEDGE SUPPORTS IT?"),
    ("expected_mechanism", "WHY SHOULD THIS CHANGE HELP?"),
    ("recommended_action", "WHAT EXACTLY WILL CHANGE?"),
    ("risk", "WHAT IS THE RISK?"),
    ("will_validate", "WHAT WILL BE VALIDATED?"),
    ("change_worked", "DID THE CHANGE ACTUALLY WORK?"),
    ("rollback", "HOW CAN IT BE ROLLED BACK?"),
)


class EmptyEvidenceError(ValueError):
    """A finding with no evidence rows must not be persisted (step 5.B.2 verify)."""


class EvidenceConfidence(str, Enum):
    DIRECT = "direct"
    DERIVED = "derived"
    HEURISTIC = "heuristic"


class EvidenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    excerpt: str
    selector: str | None = None
    value: Any = None
    confidence: EvidenceConfidence
    source_authority: str | None = None


class FindingRecord(BaseModel):
    """In-memory finding. Validates the Evidence Engine fields and the nine questions."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    observation: str
    evidence: list[EvidenceRow]
    source: str
    source_url: str
    source_authority: AuthorityLevel
    rule: str
    rule_version: int
    category: RuleCategory
    severity: RuleSeverity
    confidence: RuleConfidence
    affected_resource: str
    affected_url: str | None = None
    affected_code_entity: str | None = None
    expected_mechanism: str
    recommended_action: str
    recommendation: str
    problem: str
    actionability: str
    risk: str
    will_validate: str
    change_worked: str
    rollback: str
    status: FindingStatus = FindingStatus.OPEN

    @field_validator("evidence")
    @classmethod
    def _evidence_not_empty(cls, value: list[EvidenceRow]) -> list[EvidenceRow]:
        if not value:
            raise EmptyEvidenceError(
                "a finding whose evidence list is empty cannot be persisted"
            )
        return value

    @field_validator(
        "observation",
        "source",
        "source_url",
        "rule",
        "affected_resource",
        "expected_mechanism",
        "recommended_action",
        "recommendation",
        "problem",
        "actionability",
        "risk",
        "will_validate",
        "change_worked",
        "rollback",
        "finding_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def _answers_nine_questions(self) -> FindingRecord:
        missing = unanswered_questions(self)
        if missing:
            raise ValueError(
                "finding cannot answer: " + "; ".join(missing)
            )
        return self

    def nine_questions(self) -> dict[str, Any]:
        return {question: getattr(self, field) for field, question in NINE_QUESTIONS}


@dataclass(frozen=True)
class RuleMeta:
    rule_id: str
    version: int
    category: RuleCategory
    severity: RuleSeverity
    confidence: RuleConfidence
    source: str
    source_url: str
    authority: AuthorityLevel
    content: str
    recommendation: str

    @classmethod
    def from_rule_file(cls, rule: RuleFile, *, version: int = UNINGESTED_RULE_VERSION) -> RuleMeta:
        return cls(
            rule_id=rule.rule_id,
            version=version,
            category=rule.category,
            severity=rule.severity,
            confidence=rule.confidence,
            source=rule.source.name,
            source_url=rule.source.source_url,
            authority=rule.source.authority,
            content=rule.content,
            recommendation=rule.recommendation,
        )

    @classmethod
    def from_row(cls, row: OptimizationRule) -> RuleMeta:
        category = row.category if isinstance(row.category, RuleCategory) else RuleCategory(row.category)
        severity = row.severity if isinstance(row.severity, RuleSeverity) else RuleSeverity(row.severity)
        confidence = (
            row.confidence if isinstance(row.confidence, RuleConfidence) else RuleConfidence(row.confidence)
        )
        authority = (
            row.authority if isinstance(row.authority, AuthorityLevel) else AuthorityLevel(row.authority)
        )
        return cls(
            rule_id=row.rule_id,
            version=int(row.version),
            category=category,
            severity=severity,
            confidence=confidence,
            source=row.source,
            source_url=row.source_url,
            authority=authority,
            content=row.content,
            recommendation=row.recommendation,
        )


def unanswered_questions(record: FindingRecord) -> list[str]:
    missing: list[str] = []
    for field, question in NINE_QUESTIONS:
        value = getattr(record, field)
        if field == "evidence":
            if not value:
                missing.append(question)
            continue
        if value is None or not str(value).strip():
            missing.append(question)
    return missing


def finding_id_for(rule_id: str, affected_resource: str) -> str:
    digest = hashlib.sha1(f"{rule_id}\n{affected_resource}".encode("utf-8")).hexdigest()[:16]
    return f"{rule_id}:{digest}"


def catalog_rule_meta(*, version: int = UNINGESTED_RULE_VERSION) -> dict[tuple[str, int], RuleMeta]:
    return {
        (item.rule.rule_id, version): RuleMeta.from_rule_file(item.rule, version=version)
        for item in load_all_rule_files(KNOWLEDGE_ROOT)
    }


def load_rule_meta(db: Session) -> dict[tuple[str, int], RuleMeta]:
    """Ingested MySQL versions win; on-disk catalog fills rules not yet ingested."""

    from sqlalchemy import select

    meta = catalog_rule_meta()
    rows = list(db.scalars(select(OptimizationRule)))
    for row in rows:
        meta[(row.rule_id, int(row.version))] = RuleMeta.from_row(row)
    return meta


def resolve_rule_meta(
    hit: RuleHit,
    meta: Mapping[tuple[str, int], RuleMeta],
) -> RuleMeta | None:
    exact = meta.get((hit.rule_id, hit.rule_version))
    if exact is not None:
        return exact
    versions = [item for key, item in meta.items() if key[0] == hit.rule_id]
    if not versions:
        return None
    return max(versions, key=lambda item: item.version)


def assemble_finding(
    hit: RuleHit,
    meta: RuleMeta,
    *,
    retrieved: Sequence[CompressedEvidence] = (),
    actionability: str = "recommend_only",
    repository_facts: Mapping[str, Any] | None = None,
) -> FindingRecord:
    """Build one finding. The rule hit is the fact origin; retrieval is derived."""

    evidence = [_measured_row(hit, meta)]
    evidence.extend(_retrieved_rows(retrieved))
    observation = _observation(hit)
    mechanism = meta.content.strip()
    recommendation = meta.recommendation.strip()
    affected_url = _affected_url(hit.affected_resource)
    return FindingRecord(
        finding_id=finding_id_for(hit.rule_id, hit.affected_resource),
        observation=observation,
        evidence=evidence,
        source=meta.source,
        source_url=meta.source_url,
        source_authority=meta.authority,
        rule=hit.rule_id,
        rule_version=hit.rule_version,
        category=meta.category,
        severity=meta.severity,
        confidence=meta.confidence,
        affected_resource=hit.affected_resource,
        affected_url=affected_url,
        affected_code_entity=_affected_code_entity(hit.affected_resource, repository_facts),
        expected_mechanism=mechanism,
        recommended_action=recommendation,
        recommendation=recommendation,
        problem=observation,
        actionability=actionability,
        risk=_risk(hit, meta),
        will_validate=(
            f"Re-evaluate {hit.rule_id} against {hit.affected_resource}: {hit.expected_condition}."
        ),
        change_worked=CHANGE_WORKED_NOT_APPLIED,
        rollback=ROLLBACK_NOT_APPLICABLE,
        status=FindingStatus.OPEN,
    )


def assemble_findings(
    hits: Sequence[RuleHit],
    meta: Mapping[tuple[str, int], RuleMeta],
    *,
    retrieved_by_rule: Mapping[str, Sequence[CompressedEvidence]] | None = None,
    actionability: str = "recommend_only",
    repository_facts: Mapping[str, Any] | None = None,
) -> list[FindingRecord]:
    retrieved_by_rule = retrieved_by_rule or {}
    records: list[FindingRecord] = []
    seen: set[str] = set()
    for hit in hits:
        rule = resolve_rule_meta(hit, meta)
        if rule is None:
            logger.info("evidence skip: no rule meta for %s v%s", hit.rule_id, hit.rule_version)
            continue
        record = assemble_finding(
            hit,
            rule,
            retrieved=retrieved_by_rule.get(hit.rule_id, ()),
            actionability=actionability,
            repository_facts=repository_facts,
        )
        if record.finding_id in seen:
            continue
        seen.add(record.finding_id)
        records.append(record)
    return records


def persist_findings(
    db: Session,
    run: AnalysisRun,
    records: Sequence[FindingRecord],
) -> list[Finding]:
    """Validate and insert findings. Empty evidence cannot be persisted."""

    rows: list[Finding] = []
    for record in records:
        if not record.evidence:
            raise EmptyEvidenceError(
                f"a finding whose evidence list is empty cannot be persisted ({record.finding_id})"
            )
        FindingRecord.model_validate(record.model_dump())
        row = Finding(
            project_id=run.project_id,
            analysis_run_id=run.id,
            finding_id=record.finding_id,
            observation=record.observation,
            problem=record.problem,
            evidence=[item.model_dump(mode="json") for item in record.evidence],
            source=record.source,
            source_url=record.source_url,
            source_authority=record.source_authority,
            rule=record.rule,
            rule_version=record.rule_version,
            category=record.category,
            severity=record.severity,
            confidence=record.confidence,
            affected_resource=record.affected_resource,
            affected_url=record.affected_url,
            affected_code_entity=record.affected_code_entity,
            expected_mechanism=record.expected_mechanism,
            recommended_action=record.recommended_action,
            recommendation=record.recommendation,
            actionability=record.actionability,
            risk=record.risk,
            will_validate=record.will_validate,
            change_worked=record.change_worked,
            rollback=record.rollback,
            status=record.status,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _measured_row(hit: RuleHit, meta: RuleMeta) -> EvidenceRow:
    excerpt = (
        f"{hit.rule_id} expected {hit.expected_condition}; "
        f"observed {json.dumps(hit.observed_value, sort_keys=True, default=str)}"
    )
    return EvidenceRow(
        source=hit.affected_resource,
        excerpt=excerpt,
        selector=hit.expected_condition,
        value=hit.observed_value,
        confidence=EvidenceConfidence.DIRECT,
        source_authority=meta.authority.value,
    )


def _retrieved_rows(items: Sequence[CompressedEvidence]) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    for item in items:
        authority = item.metadata.get("authority")
        rows.append(
            EvidenceRow(
                source=item.locator,
                excerpt=item.summary,
                selector=item.locator,
                value={
                    "id": item.id,
                    "source_type": item.source_type,
                    "channel": item.channel,
                    "score": item.score,
                },
                confidence=EvidenceConfidence.DERIVED,
                source_authority=str(authority) if authority is not None else None,
            )
        )
    return rows


def _observation(hit: RuleHit) -> str:
    observed = json.dumps(hit.observed_value, sort_keys=True, default=str)
    text = (
        f"{hit.rule_id} on {hit.affected_resource}: "
        f"expected {hit.expected_condition}; observed {observed}."
    )
    # SEO-KEYWORD-FOCUS-001: name the missing keywords in plain words up
    # front, so the audit message answers "which keywords?" without the
    # reader having to parse the observed JSON.
    observed = hit.observed_value
    if (
        hit.rule_id == "SEO-KEYWORD-FOCUS-001"
        and isinstance(observed, Mapping)
        and observed.get("missing_from_title_and_description")
    ):
        missing = observed["missing_from_title_and_description"]
        if isinstance(missing, list) and missing:
            quoted = ", ".join(f'"{term}"' for term in missing)
            text += (
                f" Missing keyword(s) from title and meta description: "
                f"{quoted}."
            )
    return text


def _risk(hit: RuleHit, meta: RuleMeta) -> str:
    return (
        f"No change has been applied. Applying {hit.rule_id} "
        f"({meta.severity.value}) on {hit.affected_resource} may regress "
        "that resource. This is an ArchitectOS risk description, not a ranking predictor."
    )


def _affected_url(resource: str) -> str | None:
    token = resource.split()[0]
    if token.startswith("http://") or token.startswith("https://"):
        return token
    return None


def _affected_code_entity(
    resource: str,
    repository_facts: Mapping[str, Any] | None,
) -> str | None:
    if not repository_facts:
        return None
    if "://" in resource:
        return None
    if "/" in resource or "\\" in resource:
        return resource.split()[0]
    return None
