"""Change Set (step 8.3, `[SPEC AGENTS.md §30]`).

Groups the Change Transactions produced by one Code Agent run into one
logical, traceable unit: objective, findings, evidence, affected
resources, validation, risk, rollback information. `git_commit_ref` /
`pull_request_ref` are filled when Phase 9 publishes a logical commit/PR.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.change import ChangeSet, ChangeSetStatus


def create_change_set(
    db: Session,
    *,
    project_id: int,
    objective: str,
    description: str | None,
    finding_ids: list[str],
    evidence: list,
    affected_resources: list[str],
    risk: str,
    validation_run_id: int | None,
    rollback_info: dict,
) -> ChangeSet:
    change_set = ChangeSet(
        project_id=project_id,
        objective=objective,
        description=description,
        finding_ids_json=finding_ids,
        evidence_json=evidence,
        affected_resources_json=affected_resources,
        risk=risk,
        validation_run_id=validation_run_id,
        status=ChangeSetStatus.APPLIED,
        rollback_info_json=rollback_info,
        applied_at=datetime.now(timezone.utc),
    )
    db.add(change_set)
    db.commit()
    db.refresh(change_set)
    return change_set
