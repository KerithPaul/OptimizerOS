"""Change Set inspection + semantic rollback (step 8.7, `[SPEC AGENTS.md §37-§38]`).

`POST .../rollback` always creates a `RollbackOperation` row, even when it
stops short: a low-confidence plan without `confirmed=true` returns
`status=pending_confirmation` and writes nothing to disk. Sending the same
request again with `confirmed=true` (after the UI shows the confidence
and reasons) performs the restore.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.changes.rollback import RollbackError, build_plan, execute_rollback, resolve_target
from app.db.session import get_db
from app.models.change import ChangeSet, ChangeTransaction, RollbackOperation
from app.models.project import Project
from app.models.user import User
from app.schemas.change import (
    ChangeSetDetailOut,
    ChangeSetSummaryOut,
    ChangeTransactionOut,
    RollbackOperationOut,
    RollbackTargetIn,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["rollback"], dependencies=[Depends(require_project_access)])


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.get("/change-sets", response_model=list[ChangeSetSummaryOut])
def list_change_sets(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ChangeSetSummaryOut]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(ChangeSet).where(ChangeSet.project_id == project_id).order_by(ChangeSet.id.desc())
        )
    )
    return [ChangeSetSummaryOut.model_validate(row) for row in rows]


@router.get("/change-sets/{change_set_id}", response_model=ChangeSetDetailOut)
def get_change_set(
    project_id: int,
    change_set_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ChangeSetDetailOut:
    _get_project(db, project_id)
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None or change_set.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change set not found")
    transactions = list(
        db.scalars(
            select(ChangeTransaction)
            .where(ChangeTransaction.change_set_id == change_set_id)
            .order_by(ChangeTransaction.id)
        )
    )
    return ChangeSetDetailOut(
        id=change_set.id,
        objective=change_set.objective,
        description=change_set.description,
        finding_ids_json=change_set.finding_ids_json or [],
        evidence_json=change_set.evidence_json,
        affected_resources_json=change_set.affected_resources_json or [],
        risk=change_set.risk,
        status=change_set.status,
        git_commit_ref=change_set.git_commit_ref,
        pull_request_ref=change_set.pull_request_ref,
        rollback_info_json=change_set.rollback_info_json,
        baseline_metrics_json=change_set.baseline_metrics_json,
        treatment_metrics_json=change_set.treatment_metrics_json,
        created_at=change_set.created_at,
        applied_at=change_set.applied_at,
        transactions=[ChangeTransactionOut.model_validate(t) for t in transactions],
    )


@router.post("/rollback", response_model=RollbackOperationOut, status_code=status.HTTP_201_CREATED)
def rollback(
    project_id: int,
    payload: RollbackTargetIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RollbackOperationOut:
    _get_project(db, project_id)
    try:
        target = resolve_target(
            db,
            project_id=project_id,
            change_set_id=payload.change_set_id,
            change_transaction_id=payload.change_transaction_id,
            change_item_id=payload.change_item_id,
            symbol_name=payload.symbol_name,
            category=payload.category,
        )
        plan, _repository, requested_target = build_plan(db, target=target)
    except RollbackError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    operation = execute_rollback(
        db,
        project_id=project_id,
        target=target,
        plan=plan,
        requested_target=requested_target,
        confirmed=payload.confirmed,
    )
    return RollbackOperationOut.model_validate(operation)


@router.get("/rollback/{operation_id}", response_model=RollbackOperationOut)
def get_rollback_operation(
    project_id: int,
    operation_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RollbackOperationOut:
    _get_project(db, project_id)
    operation = db.get(RollbackOperation, operation_id)
    if operation is None or operation.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rollback operation not found")
    return RollbackOperationOut.model_validate(operation)
