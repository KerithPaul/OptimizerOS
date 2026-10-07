"""Experiments API (step 11.4). Result payloads never claim causation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.db.session import get_db
from app.models.project import Project
from app.models.search import Experiment
from app.models.user import User
from app.schemas.search import ExperimentCreateIn, ExperimentOut
from app.services.experiments import ExperimentError, create_experiment, record_treatment
from app.services.measurement import CAUSATION_NOT_CLAIMED, CAUSATION_NOTE

router = APIRouter(prefix="/projects/{project_id}/experiments", tags=["experiments"], dependencies=[Depends(require_project_access)])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


def _out(row: Experiment) -> ExperimentOut:
    payload = ExperimentOut.model_validate(row)
    return payload.model_copy(
        update={
            "causation": CAUSATION_NOT_CLAIMED,
            "causation_note": CAUSATION_NOTE,
        }
    )


@router.get("", response_model=list[ExperimentOut])
def list_experiments(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ExperimentOut]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(Experiment)
            .where(Experiment.project_id == project_id)
            .order_by(Experiment.id.desc())
        )
    )
    return [_out(row) for row in rows]


@router.post("", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED)
def create_experiment_endpoint(
    project_id: int,
    payload: ExperimentCreateIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ExperimentOut:
    _get_project(db, project_id)
    try:
        row = create_experiment(
            db,
            project_id=project_id,
            hypothesis=payload.hypothesis,
            experiment_type=payload.experiment_type,
            change_set_id=payload.change_set_id,
            finding_ids=payload.finding_ids,
            fetch_gsc=True,
        )
    except ExperimentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _out(row)


@router.get("/{experiment_id}", response_model=ExperimentOut)
def get_experiment(
    project_id: int,
    experiment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ExperimentOut:
    _get_project(db, project_id)
    row = db.get(Experiment, experiment_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    return _out(row)


@router.post("/{experiment_id}/measure-treatment", response_model=ExperimentOut)
def measure_treatment(
    project_id: int,
    experiment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ExperimentOut:
    _get_project(db, project_id)
    row = db.get(Experiment, experiment_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    return _out(record_treatment(db, row, fetch_gsc=True))
