"""Analysis runs, scores, and findings (step 5.C.3).

Serves the optimization dashboard's three scores (5.C.1) and the findings
list with filters, evidence panel, and priority (5.C.2). Findings are
always returned ranked by priority so the list itself is the
prioritisation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.models.finding import AnalysisRun, Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project
from app.models.user import User
from app.retrieval.prioritize import prioritize_findings
from app.schemas.finding import AnalysisRunOut, FindingOut, PriorityFactorsOut

router = APIRouter(prefix="/projects/{project_id}", tags=["findings"], dependencies=[Depends(require_project_access)])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.get("/analysis-runs", response_model=list[AnalysisRunOut])
def list_analysis_runs(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AnalysisRun]:
    _get_project(db, project_id)
    return list(
        db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.project_id == project_id)
            .order_by(AnalysisRun.id.desc())
        )
    )


@router.get("/analysis-runs/latest", response_model=AnalysisRunOut | None)
def get_latest_analysis_run(
    project_id: int,
    optional: bool = Query(False, description="Return null instead of 404 when no run exists yet"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AnalysisRun | None:
    return _require_latest_run(db, project_id, optional=optional)


@router.get("/findings", response_model=list[FindingOut])
def list_findings(
    project_id: int,
    run_id: int | None = Query(None),
    category: RuleCategory | None = Query(None),
    severity: RuleSeverity | None = Query(None),
    confidence: RuleConfidence | None = Query(None),
    status_: FindingStatus | None = Query(None, alias="status"),
    url: str | None = Query(None, min_length=1),
    rule: str | None = Query(None, min_length=1),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    _get_project(db, project_id)
    run = (
        db.get(AnalysisRun, run_id)
        if run_id is not None
        else db.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.project_id == project_id)
            .order_by(AnalysisRun.id.desc())
        )
    )
    if run is None or run.project_id != project_id:
        return []

    stmt = select(Finding).where(Finding.analysis_run_id == run.id)
    if category is not None:
        stmt = stmt.where(Finding.category == category)
    if severity is not None:
        stmt = stmt.where(Finding.severity == severity)
    if confidence is not None:
        stmt = stmt.where(Finding.confidence == confidence)
    if status_ is not None:
        stmt = stmt.where(Finding.status == status_)
    if rule is not None:
        stmt = stmt.where(Finding.rule == rule)
    rows = list(db.scalars(stmt))
    if url:
        needle = url.lower()
        rows = [
            row
            for row in rows
            if needle in row.affected_resource.lower()
            or (row.affected_url is not None and needle in row.affected_url.lower())
        ]
    return _findings_out(rows)


@router.get("/findings/{finding_id}", response_model=FindingOut)
def get_finding(
    project_id: int,
    finding_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    _get_project(db, project_id)
    row = db.get(Finding, finding_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    run = db.get(AnalysisRun, row.analysis_run_id)
    siblings = list(db.scalars(select(Finding).where(Finding.analysis_run_id == run.id)))
    return _findings_out(siblings, only=row.id)[0]


def _require_latest_run(
    db: Session, project_id: int, *, optional: bool = False
) -> AnalysisRun | None:
    _get_project(db, project_id)
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project_id)
        .order_by(AnalysisRun.id.desc())
    )
    if run is None:
        if optional:
            return None
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no analysis run yet")
    return run


def _findings_out(rows: list[Finding], *, only: int | None = None) -> list[dict]:
    """Rank `rows` by priority together, so factors like `reach` reflect the whole run."""

    ranked = {result.finding_id: result for result in prioritize_findings(rows)}
    ordered = sorted(
        rows,
        key=lambda row: ranked[row.finding_id].priority,
        reverse=True,
    )
    if only is not None:
        ordered = [row for row in ordered if row.id == only]
    out: list[dict] = []
    for row in ordered:
        result = ranked[row.finding_id]
        payload = {
            column.name: getattr(row, column.name) for column in Finding.__table__.columns
        }
        payload["priority"] = result.priority
        payload["priority_factors"] = PriorityFactorsOut(
            impact=result.factors.impact,
            confidence=result.factors.confidence,
            reach=result.factors.reach,
            actionability=result.factors.actionability,
            risk=result.factors.risk,
            reach_input=result.factors.reach_input,
        )
        out.append(payload)
    return out
