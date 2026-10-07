"""Optimization experiment engine (step 11.4).

Model `[SPEC AGENTS.md §48]`:
Hypothesis → Baseline → Change → Validation → Post-change measurement → Result.

Stores experiment, hypothesis, change, baseline_metrics, treatment_metrics,
result, confidence. Candidate kinds: title, FAQ, content restructure,
schema, internal links.

Never confuse correlation with causation. The stored result and the UI
copy must not present a metric movement as proof the change caused it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.change import ChangeSet
from app.models.finding import Finding
from app.models.search import Experiment, ExperimentStatus, ExperimentType
from app.services.measurement import (
    CAUSATION_NOT_CLAIMED,
    CAUSATION_NOTE,
    capture_metrics,
    compare_metrics,
)

RULE_TYPE_PREFIXES: tuple[tuple[str, ExperimentType], ...] = (
    ("TITLE", ExperimentType.TITLE),
    ("FAQ", ExperimentType.FAQ),
    ("STRUCTUREDDATA", ExperimentType.SCHEMA),
    ("SCHEMA", ExperimentType.SCHEMA),
    ("ORPHAN", ExperimentType.INTERNAL_LINKS),
    ("LINK", ExperimentType.INTERNAL_LINKS),
    ("CONTENT", ExperimentType.CONTENT_RESTRUCTURE),
)


class ExperimentError(Exception):
    pass


def infer_experiment_type(rule_id: str | None) -> ExperimentType | None:
    if not rule_id:
        return None
    upper = rule_id.upper()
    for needle, kind in RULE_TYPE_PREFIXES:
        if needle in upper:
            return kind
    return None


def create_experiment(
    db: Session,
    *,
    project_id: int,
    hypothesis: str,
    experiment_type: ExperimentType,
    change_set_id: int | None = None,
    finding_ids: list[str] | None = None,
    fetch_gsc: bool = False,
) -> Experiment:
    cleaned = hypothesis.strip()
    if not cleaned:
        raise ExperimentError("hypothesis must not be empty")
    change_set = None
    if change_set_id is not None:
        change_set = db.get(ChangeSet, change_set_id)
        if change_set is None or change_set.project_id != project_id:
            raise ExperimentError("change set not found")
    urls = list(change_set.affected_resources_json or []) if change_set is not None else []
    ids = finding_ids or (list(change_set.finding_ids_json or []) if change_set is not None else [])
    baseline = capture_metrics(
        db,
        project_id,
        finding_ids=ids,
        urls=urls,
        fetch_gsc=fetch_gsc,
    )
    now = datetime.now(timezone.utc)
    status = ExperimentStatus.HYPOTHESIS
    change_json = None
    if change_set is not None:
        change_json = {
            "change_set_id": change_set.id,
            "objective": change_set.objective,
            "finding_ids": change_set.finding_ids_json,
            "affected_resources": change_set.affected_resources_json,
            "validation_run_id": change_set.validation_run_id,
        }
        status = ExperimentStatus.VALIDATION
        change_set.baseline_metrics_json = baseline
    experiment = Experiment(
        project_id=project_id,
        change_set_id=change_set_id,
        experiment_type=experiment_type,
        hypothesis=cleaned,
        change_json=change_json,
        baseline_metrics=baseline,
        treatment_metrics=None,
        result=None,
        confidence=None,
        status=status,
        baseline_recorded_at=now,
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def record_baseline_for_change_set(
    db: Session,
    change_set: ChangeSet,
    *,
    finding: Finding | None = None,
    fetch_gsc: bool = False,
) -> Experiment | None:
    """Attach baseline metrics to a completed Change Set.

    Creates an experiment when the finding's rule maps to a candidate
    kind. Baseline metrics are stored on the Change Set either way.
    """

    kind = infer_experiment_type(finding.rule if finding is not None else None)
    hypothesis = finding.expected_mechanism if finding is not None else change_set.objective
    existing = db.scalar(
        select(Experiment).where(Experiment.change_set_id == change_set.id)
    )
    if existing is not None:
        baseline = capture_metrics(
            db,
            change_set.project_id,
            finding_ids=list(change_set.finding_ids_json or []),
            urls=list(change_set.affected_resources_json or []),
            fetch_gsc=fetch_gsc,
        )
        change_set.baseline_metrics_json = baseline
        existing.baseline_metrics = baseline
        existing.hypothesis = hypothesis
        existing.status = ExperimentStatus.VALIDATION
        existing.baseline_recorded_at = datetime.now(timezone.utc)
        existing.change_json = {
            "change_set_id": change_set.id,
            "objective": change_set.objective,
            "finding_ids": change_set.finding_ids_json,
            "affected_resources": change_set.affected_resources_json,
            "validation_run_id": change_set.validation_run_id,
        }
        db.commit()
        db.refresh(existing)
        return existing
    if kind is None:
        baseline = capture_metrics(
            db,
            change_set.project_id,
            finding_ids=list(change_set.finding_ids_json or []),
            urls=list(change_set.affected_resources_json or []),
            fetch_gsc=fetch_gsc,
        )
        change_set.baseline_metrics_json = baseline
        db.commit()
        db.refresh(change_set)
        return None
    return create_experiment(
        db,
        project_id=change_set.project_id,
        hypothesis=hypothesis,
        experiment_type=kind,
        change_set_id=change_set.id,
        finding_ids=list(change_set.finding_ids_json or []),
        fetch_gsc=fetch_gsc,
    )


def record_treatment(
    db: Session,
    experiment: Experiment,
    *,
    fetch_gsc: bool = True,
) -> Experiment:
    """Post-change measurement → Result. Causation is never claimed."""

    change_set = (
        db.get(ChangeSet, experiment.change_set_id)
        if experiment.change_set_id is not None
        else None
    )
    urls = list(change_set.affected_resources_json or []) if change_set is not None else []
    ids = list(change_set.finding_ids_json or []) if change_set is not None else []
    treatment = capture_metrics(
        db,
        experiment.project_id,
        finding_ids=ids,
        urls=urls,
        fetch_gsc=fetch_gsc,
    )
    now = datetime.now(timezone.utc)
    experiment.treatment_metrics = treatment
    experiment.treatment_recorded_at = now
    experiment.status = ExperimentStatus.POST_CHANGE_MEASUREMENT
    if change_set is not None:
        change_set.treatment_metrics_json = treatment
    baseline = experiment.baseline_metrics or {}
    comparison = compare_metrics(baseline, treatment)
    confidence = _result_confidence(baseline, treatment)
    experiment.result = {
        **comparison,
        "confidence": confidence,
        "causation": CAUSATION_NOT_CLAIMED,
        "causation_note": CAUSATION_NOTE,
    }
    experiment.confidence = confidence
    experiment.status = ExperimentStatus.RESULT
    experiment.finished_at = now
    db.commit()
    db.refresh(experiment)
    return experiment


def _result_confidence(baseline: dict, treatment: dict) -> float:
    """[PROPOSED] inspectability of the comparison, not causal confidence.

    Higher when both snapshots have measured GSC impressions. Always a
    statement about data completeness, never about whether the change
    caused the movement.
    """

    def _impressions(snapshot: dict) -> int | None:
        metrics = (snapshot or {}).get("metrics") or {}
        item = metrics.get("impressions") or {}
        if item.get("status") != "measured":
            return None
        value = item.get("value")
        return int(value) if isinstance(value, (int, float)) else None

    before = _impressions(baseline)
    after = _impressions(treatment)
    if before is None or after is None:
        return 0.2
    sample = min(before, after)
    if sample >= 10_000:
        return 0.7
    if sample >= 1_000:
        return 0.5
    if sample >= 100:
        return 0.35
    return 0.25
