"""Code change creation, listing, and review (step 7.10).

`POST .../changes/apply` mirrors `app.api.v1.agents.start_agent_run`: it
creates the `jobs` row and the seed `agent_runs` row (`AgentType.CODE`,
carrying `{finding_id, source_agent_run_id, dry_run}` in `objective_json`)
inside one transaction, before the job id reaches Redis, so the worker
(`app.jobs.handlers.code_change`) can never observe a job with no seed
row to claim.
"""

from __future__ import annotations

import difflib

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.access import require_project_access
from app.api.v1.auth import get_current_user
from app.changes.actionability import CODE_ACTIONABLE, effective_actionability
from app.changes.inspect import InspectedFile, InspectionResult, inspect_finding_code
from app.changes.scope import is_path_safe
from app.changes.snapshot import SnapshotError, create_file_snapshot
from app.connectors.capabilities import MODES_REQUIRING_MODIFICATION, WORDPRESS_PLATFORM
from app.core.config import get_settings
from app.db.session import get_db
from app.intelligence.repository.clone import workspace_path
from app.jobs.queue import get_redis
from app.models.agent import AgentRun, AgentRunStatus, AgentType
from app.models.change import Snapshot, SnapshotReason, ValidationResult, ValidationRun
from app.models.finding import Finding
from app.models.job import Job, JobStatus
from app.models.project import Project
from app.models.repository import CloneStatus, Repository
from app.models.user import User
from app.models.website import Website
from app.schemas.change import (
    ApplyChangeCreate,
    ChangeDetailOut,
    ChangeFindingOut,
    ChangeJobOut,
    ChangeSummaryOut,
    SnapshotFileCreate,
    ValidationResultOut,
    ValidationRunOut,
)

router = APIRouter(prefix="/projects/{project_id}/changes", tags=["changes"], dependencies=[Depends(require_project_access)])

JOB_TYPE = "code_change"

# Guided-fix STEP 3 payload cap. The full original file is stored on disk
# (immutable) and never re-uploaded; only bounded excerpts travel over the API.
_INSPECT_FILE_CHARS = 40_000


def _normalize_diff_preview(value: object) -> dict | None:
    """Older runs stored `{}` when the Code Agent produced no patch.

    An empty object is truthy in the UI, so `diff_preview.files.length`
    crashed the change-review page. Always return the `{notes, files}`
    shape the frontend expects, or None when nothing was stored.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"notes": "", "files": []}
    files = value.get("files")
    notes = value.get("notes")
    return {
        "notes": notes if isinstance(notes, str) else "",
        "files": files if isinstance(files, list) else [],
    }


def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


@router.post("/apply", response_model=ChangeJobOut, status_code=status.HTTP_201_CREATED)
def apply_change(
    project_id: int,
    payload: ApplyChangeCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ChangeJobOut:
    project = _get_project(db, project_id)

    finding = db.scalar(
        select(Finding)
        .where(Finding.project_id == project_id, Finding.finding_id == payload.finding_id)
        .order_by(Finding.id.desc())
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")

    website = db.scalar(select(Website).where(Website.project_id == project_id))
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    actionability = effective_actionability(
        finding.actionability, repository=repository, website=website
    )
    if actionability not in CODE_ACTIONABLE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "this finding has no associated code or platform change (actionability="
            f"{finding.actionability!r}); attach and clone a repository, or re-run the "
            "audit once one is attached",
        )

    source_run = db.get(AgentRun, payload.source_agent_run_id)
    if source_run is None or source_run.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source agent run not found")

    wordpress = website is not None and website.platform == WORDPRESS_PLATFORM
    if repository is None and not wordpress:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no repository attached to this project")
    dry_run = project.mode not in MODES_REQUIRING_MODIFICATION
    if (
        not dry_run
        and repository is not None
        and not wordpress
        and repository.clone_status != CloneStatus.CLONED
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"repository is not cloned yet (clone_status={repository.clone_status.value})",
        )

    job_type = "cms_change" if wordpress and repository is None else JOB_TYPE
    agent_type = AgentType.CMS if job_type == "cms_change" else AgentType.CODE
    settings = get_settings()
    job = Job(project_id=project_id, type=job_type, status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)

    seed = AgentRun(
        project_id=project_id,
        job_id=job.id,
        agent_type=agent_type,
        status=AgentRunStatus.RUNNING,
        request_text=finding.recommended_action,
        objective_json={
            "finding_id": payload.finding_id,
            "source_agent_run_id": payload.source_agent_run_id,
            "dry_run": dry_run,
        },
    )
    db.add(seed)
    db.commit()

    get_redis(settings).lpush(settings.job_queue_key, str(job.id))
    return ChangeJobOut(job_id=job.id, dry_run=dry_run)


@router.get("", response_model=list[ChangeSummaryOut])
def list_changes(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ChangeSummaryOut]:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(AgentRun)
            .where(
                AgentRun.project_id == project_id,
                AgentRun.agent_type.in_((AgentType.CODE, AgentType.CMS)),
            )
            .order_by(AgentRun.id.desc())
        )
    )
    seen: set[str] = set()
    out: list[ChangeSummaryOut] = []
    for row in rows:
        objective = row.objective_json or {}
        finding_id = objective.get("finding_id")
        if not finding_id or finding_id in seen:
            continue
        seen.add(finding_id)
        result = row.result_json or {}
        out.append(
            ChangeSummaryOut(
                agent_run_id=row.id,
                job_id=row.job_id,
                finding_id=finding_id,
                status=row.status,
                dry_run=result.get("dry_run") if isinstance(result, dict) else None,
                stopped_reason=row.stopped_reason,
                approved=result.get("approved") if isinstance(result, dict) else None,
                created_at=row.created_at,
                finished_at=row.finished_at,
            )
        )
    return out


@router.get("/{finding_id}", response_model=ChangeDetailOut)
def get_change_detail(
    project_id: int,
    finding_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ChangeDetailOut:
    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(AgentRun)
            .where(
                AgentRun.project_id == project_id,
                AgentRun.agent_type.in_((AgentType.CODE, AgentType.CMS)),
            )
            .order_by(AgentRun.id.desc())
        )
    )
    row = next(
        (r for r in rows if (r.objective_json or {}).get("finding_id") == finding_id), None
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no change for this finding")

    result: dict = row.result_json if isinstance(row.result_json, dict) else {}
    finding = db.scalar(
        select(Finding)
        .where(Finding.project_id == project_id, Finding.finding_id == finding_id)
        .order_by(Finding.id.desc())
    )

    validation_run_out = None
    validation_run_id = result.get("validation_run_id")
    if validation_run_id is not None:
        vrun = db.get(ValidationRun, validation_run_id)
        if vrun is not None:
            results = list(
                db.scalars(
                    select(ValidationResult).where(ValidationResult.validation_run_id == vrun.id)
                )
            )
            validation_run_out = ValidationRunOut(
                id=vrun.id,
                status=vrun.status,
                affected_urls_json=vrun.affected_urls_json,
                gaps_json=vrun.gaps_json,
                error=vrun.error,
                created_at=vrun.created_at,
                finished_at=vrun.finished_at,
                results=[ValidationResultOut.model_validate(r) for r in results],
            )

    finding_out = None
    if finding is not None:
        finding_out = ChangeFindingOut(
            observation=finding.observation,
            evidence=finding.evidence or [],
            risk=finding.risk,
            recommended_action=finding.recommended_action,
            affected_resource=finding.affected_resource,
            affected_url=finding.affected_url,
            affected_code_entity=finding.affected_code_entity,
            expected_mechanism=finding.expected_mechanism,
            status=finding.status.value,
        )

    return ChangeDetailOut(
        agent_run_id=row.id,
        job_id=row.job_id,
        finding_id=finding_id,
        status=row.status,
        error=row.error,
        dry_run=result.get("dry_run"),
        stopped_reason=row.stopped_reason,
        violation=result.get("violation"),
        change_plan=result.get("change_plan"),
        execution_plan=result.get("execution_plan"),
        validation_plan=result.get("validation_plan"),
        diff_preview=_normalize_diff_preview(result.get("diff_preview")),
        validation_run=validation_run_out,
        reviewer_verdict=result.get("reviewer_verdict"),
        approved=result.get("approved"),
        finding_status=finding.status.value if finding is not None else "unknown",
        finding=finding_out,
        validation_skipped=result.get("validation_skipped"),
        reviewer_skipped=result.get("reviewer_skipped"),
        workspace_files=result.get("workspace_files"),
        platform_notes=result.get("platform_notes"),
        repair_attempts=result.get("repair_attempts"),
    )


# ---------------------------------------------------------------------------
# Guided fix workflow (inspect → snapshot → apply → compare), built on top
# of the existing pipeline above. Every route here is additive: the apply,
# list, and detail contracts the changes UI already uses are untouched.
# ---------------------------------------------------------------------------


def _inspected_file_out(item: InspectedFile) -> dict:
    return {
        "path": item.path,
        "status": item.status,
        "content": item.content[:_INSPECT_FILE_CHARS],
        "locator_hits": item.locator_hits,
    }


def _inspection_out(result: InspectionResult) -> dict:
    return {
        "finding_id": result.finding_id,
        "repository_attached": result.repository_attached,
        "workspace_ready": result.workspace_ready,
        "files": [_inspected_file_out(item) for item in result.files],
        "locators": result.locators,
        "detected_issue": result.detected_issue,
        "recommended_action": result.recommended_action,
        "applies": result.applies,
        "applies_reason": result.applies_reason,
        "actionability": result.actionability,
    }


@router.get("/{finding_id}/inspect")
def inspect_change(
    project_id: int,
    finding_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Read-only inspection of the finding's current implementation (STEP 1/2).

    Locates the real files behind the recommendation in the cloned
    workspace, reads their current content, and reports whether the
    recommendation applies. Never plans, never writes, never snapshots —
    the response is safe to re-run any time.
    """

    _get_project(db, project_id)
    finding = db.scalar(
        select(Finding)
        .where(Finding.project_id == project_id, Finding.finding_id == finding_id)
        .order_by(Finding.id.desc())
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    result = inspect_finding_code(db, project_id=project_id, finding=finding, repository=repository)
    result.actionability = effective_actionability(
        result.actionability, repository=repository, website=website
    )
    return _inspection_out(result)


@router.post("/{finding_id}/snapshot-file", status_code=status.HTTP_201_CREATED)
def snapshot_original_file(
    project_id: int,
    finding_id: str,
    payload: SnapshotFileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Persist the exact pre-modification content of one file (STEP 3).

    The content is written once to the immutable snapshots tree and
    recorded in `snapshots.files_json`; the endpoint is idempotent —
    re-running it never overwrites an existing original (the first
    snapshot of a finding+file pair stays the original of record).
    """

    _get_project(db, project_id)
    finding = db.scalar(
        select(Finding)
        .where(Finding.project_id == project_id, Finding.finding_id == finding_id)
        .order_by(Finding.id.desc())
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if repository is None or repository.clone_status != CloneStatus.CLONED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "repository is not attached or not cloned"
        )
    if not is_path_safe(payload.file_path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsafe file path")

    settings = get_settings()
    workspace = workspace_path(project_id, repository.id, settings)
    try:
        snapshot, created = create_file_snapshot(
            db,
            repository=repository,
            finding_id=finding_id,
            file_path=payload.file_path,
            settings=settings,
        )
    except SnapshotError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    rel = payload.file_path.replace("\\", "/")
    original: str | None = None
    for row in snapshot.files_json or []:
        if isinstance(row, dict) and row.get("file_path") == rel:
            original = row.get("content")
            break
    return {
        "snapshot_id": snapshot.id,
        "file_path": rel,
        "created": created,
        "original_content": original,
        "commit_hash": snapshot.commit_hash,
        "captured_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


def _file_before_after_from_snapshot(
    snapshot: Snapshot, rel_path: str
) -> tuple[str | None, str | None, str | None]:
    """(original, current, error) for one file recorded in a snapshot."""

    rel = rel_path.replace("\\", "/")
    original = None
    for row in snapshot.files_json or []:
        if isinstance(row, dict) and row.get("file_path") == rel:
            original = row.get("content")
            break
    if original is None:
        return None, None, "file not recorded in this snapshot"
    if snapshot.repository_id is None:
        return original, None, "snapshot has no repository_id"
    settings = get_settings()
    workspace = workspace_path(snapshot.project_id, snapshot.repository_id, settings)
    full = workspace / rel
    if not full.is_file():
        return original, None, None  # the change removed the file
    try:
        current = full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return original, None, f"could not read current file: {exc}"
    return original, current, None


@router.get("/{finding_id}/file-change")
def get_file_change(
    project_id: int,
    finding_id: str,
    file_path: str,
    snapshot_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Exact before/after content for one file of a finding (STEP 5).

    `before` is the immutable pre-modification content recorded in the
    snapshot; `after` is what the file contains now. No synthesized
    content: both sides are read from the recorded original and the real
    workspace file.
    """

    _get_project(db, project_id)
    rel = file_path.replace("\\", "/")
    query = select(Snapshot).where(
        Snapshot.project_id == project_id,
        Snapshot.reason == SnapshotReason.BEFORE_GUIDED_FIX,
    )
    if snapshot_id is not None:
        query = query.where(Snapshot.id == snapshot_id)
    rows = list(db.scalars(query.order_by(Snapshot.id.desc())))
    snapshot = next(
        (s for s in rows if any(
            isinstance(row, dict) and row.get("file_path") == rel
            for row in (s.files_json or [])
        )),
        None,
    )
    if snapshot is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no pre-fix snapshot records this file for the finding",
        )

    original, current, error = _file_before_after_from_snapshot(snapshot, rel)
    changed = original is not None and current is not None and original != current
    return {
        "finding_id": finding_id,
        "file_path": rel,
        "snapshot_id": snapshot.id,
        "before": (original or "")[:_INSPECT_FILE_CHARS],
        "after": (current or "")[:_INSPECT_FILE_CHARS],
        "changed": changed,
        "error": error,
        "captured_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


@router.get("/{finding_id}/changes-summary")
def get_changes_summary(
    project_id: int,
    finding_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """File-level change summary for the finding's guided fixes (STEP 6).

    One entry per guided-fix snapshot that recorded a file for this
    finding: whether the file actually changed since its original was
    captured, and the diff size. Empty `files` means the recommendation
    was inspected but nothing was modified.
    """

    _get_project(db, project_id)
    rows = list(
        db.scalars(
            select(Snapshot)
            .where(
                Snapshot.project_id == project_id,
                Snapshot.reason == SnapshotReason.BEFORE_GUIDED_FIX,
            )
            .order_by(Snapshot.id.desc())
        )
    )
    files: list[dict] = []
    for snapshot in rows:
        for row in snapshot.files_json or []:
            if not (isinstance(row, dict) and row.get("finding_id") == finding_id):
                continue
            rel = str(row.get("file_path") or "")
            if not rel or any(item["file_path"] == rel for item in files):
                continue
            original, current, error = _file_before_after_from_snapshot(snapshot, rel)
            diff = list(difflib.unified_diff(
                (original or "").splitlines(keepends=True),
                (current or "").splitlines(keepends=True),
            )) if (original is not None and current is not None) else []
            files.append({
                "file_path": rel,
                "snapshot_id": snapshot.id,
                "changed": original is not None and current is not None and original != current,
                "lines_added": sum(
                    1 for line in diff if line.startswith("+") and not line.startswith("+++")
                ),
                "lines_removed": sum(
                    1 for line in diff if line.startswith("-") and not line.startswith("---")
                ),
                "error": error,
            })
    return {"finding_id": finding_id, "files": files}
