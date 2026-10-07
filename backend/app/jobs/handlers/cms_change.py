"""CMS change job (step 10.4 / 10.6). WordPress only. Reuses the Reviewer Agent."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import default_budget
from app.agents.cms import CmsAgentResult, cms_change_plan, run_cms_agent
from app.agents.reviewer import run_reviewer_agent
from app.changes.changeset import create_change_set
from app.changes.preview import compose_diff_summary
from app.changes.transaction import record_transaction
from app.connectors.capabilities import MODES_REQUIRING_MODIFICATION, WORDPRESS_PLATFORM
from app.connectors.wordpress.adapters.base import AdapterFieldError
from app.connectors.wordpress.auth import WordPressAuthError
from app.connectors.wordpress.connector import WordPressConnector
from app.connectors.wordpress.rest import WordPressApiError
from app.connectors.wordpress.snapshot import WordPressSnapshotError
from app.connectors.wordpress.validate import run_cms_validation
from app.core.config import get_settings
from app.jobs.registry import ProgressReporter, register
from app.llm.gateway import LLMGateway
from app.models.agent import AgentMessage, AgentMessageRole, AgentRun, AgentRunStatus, AgentType
from app.models.change import ChangePlatform, ChangeSet, ValidationRunStatus
from app.models.finding import Finding, FindingStatus
from app.models.job import Job
from app.models.project import Project, ProjectMode
from app.models.website import Website
from app.planners.optimization import Intervention
from app.services.experiments import record_baseline_for_change_set

_ROLLBACK_METHOD = "wordpress_revision + architectos_snapshot"


def cms_change(job: Job, db: Session, report_progress: ProgressReporter) -> None:
    seed = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CMS)
    )
    if seed is None:
        raise RuntimeError("no seeded cms agent_runs row for this job")

    objective = seed.objective_json or {}
    finding_id = objective.get("finding_id")
    source_agent_run_id = objective.get("source_agent_run_id")
    if not finding_id or not source_agent_run_id:
        raise RuntimeError("cms_change job requires finding_id and source_agent_run_id")

    seed.started_at = datetime.now(timezone.utc)
    db.commit()

    project = db.get(Project, job.project_id)
    if project is None:
        raise RuntimeError("project not found")
    dry_run = project.mode not in MODES_REQUIRING_MODIFICATION
    live = project.mode is ProjectMode.COMMIT

    if project.mode is ProjectMode.CREATE_PR:
        raise RuntimeError("CREATE_PR is not a WordPress capability")

    website = db.scalar(select(Website).where(Website.project_id == job.project_id))
    if website is None or website.platform != WORDPRESS_PLATFORM:
        raise RuntimeError("no WordPress website attached to this project")

    finding = db.scalar(
        select(Finding)
        .where(Finding.project_id == job.project_id, Finding.finding_id == finding_id)
        .order_by(Finding.id.desc())
    )
    if finding is None:
        raise RuntimeError(f"finding not found: {finding_id}")
    intervention = _load_intervention(db, job.project_id, source_agent_run_id, finding_id)
    if intervention is None:
        raise RuntimeError(
            f"no intervention for finding_id={finding_id} on agent_run_id={source_agent_run_id}"
        )

    settings = get_settings()
    gateway = LLMGateway(settings)
    budget = default_budget(settings)

    report_progress("Connecting", 8, "Authenticating with WordPress")
    try:
        connector = WordPressConnector.from_project(
            db, job.project_id, website.url, settings=settings
        )
        connector.authenticate()
    except WordPressAuthError as exc:
        raise RuntimeError(f"WordPress authentication failure: {exc}") from exc
    except WordPressApiError as exc:
        raise RuntimeError(f"WordPress API failure: {exc}") from exc

    report_progress("Planning", 15, "CMS Change Plan")
    change_plan = cms_change_plan(finding, intervention)

    report_progress("Mutating", 30, "CMS/Platform Agent")
    cms_result = run_cms_agent(
        finding=finding,
        intervention=intervention,
        change_plan=change_plan,
        connector=connector,
        gateway=gateway,
        budget=budget,
    )

    if dry_run or cms_result.patch is None:
        reviewer_row = _new_reviewer_run(db, job, {"finding_id": finding_id, "dry_run": True})
        reviewer_outcome, verdict = run_reviewer_agent(
            finding=finding,
            change_plan=change_plan,
            diff_summary=_diff_summary(cms_result),
            actual_files=_urls(cms_result),
            validation_summary=[],
            gateway=gateway,
            budget=budget,
            dry_run=True,
            diff_files=_diff_files(cms_result),
        )
        _persist_agent_run(db, reviewer_row, reviewer_outcome, result_json=reviewer_outcome.output or {})
        _persist_agent_run(
            db,
            seed,
            cms_result.outcome,
            result_json={
                "platform": WORDPRESS_PLATFORM,
                "dry_run": True,
                "diff_preview": _diff_preview(cms_result),
                "reviewer_agent_run_id": reviewer_row.id,
                "reviewer_verdict": verdict.model_dump() if verdict is not None else None,
                "error": cms_result.error,
            },
        )
        report_progress("Finishing", 100, "CMS diff preview generated (mode does not permit applying it)")
        return

    snapshot = None
    try:
        report_progress("Snapshot", 40, "ArchitectOS WordPress snapshot before mutation")
        snapshot = connector.persist_snapshot(db, job=job)
    except WordPressSnapshotError as exc:
        finding.status = FindingStatus.OPEN
        db.commit()
        _persist_agent_run(
            db,
            seed,
            cms_result.outcome,
            result_json={"error": str(exc)},
        )
        raise RuntimeError(f"WordPress snapshot failed: {exc}") from exc

    applied_live = False
    if live:
        report_progress("Applying", 55, "Publishing through the matching WordPress adapter")
        try:
            for mutation in cms_result.patch.mutations:
                connector.update_metadata(mutation.url, {mutation.field: mutation.new_value})
            applied_live = True
        except (AdapterFieldError, WordPressAuthError, WordPressApiError) as exc:
            if isinstance(exc, WordPressAuthError):
                message = f"WordPress authentication failure: {exc}"
            elif isinstance(exc, WordPressApiError):
                message = f"WordPress API failure: {exc}"
            else:
                message = str(exc)
            finding.status = FindingStatus.REJECTED
            db.commit()
            _persist_agent_run(
                db,
                seed,
                cms_result.outcome,
                result_json={"error": message, "snapshot_id": snapshot.id},
            )
            raise RuntimeError(message) from exc
    else:
        report_progress("Applying", 55, "APPLY_LOCALLY: snapshot only, production not written")

    report_progress("Validating", 70, "Re-fetch WordPress REST and re-run the finding rule")
    from app.models.change import ValidationResult

    overlay = None if live else _overlay_pages(connector, cms_result)
    validation_run = run_cms_validation(
        db,
        finding=finding,
        connector=connector,
        urls=_urls(cms_result),
        agent_run_id=seed.id,
        snapshot_id=snapshot.id,
        live=live,
        overlay_pages=overlay,
    )
    validation_summary = [
        {"check_type": row.check_type.value, "status": row.status.value, "detail": row.detail}
        for row in db.scalars(
            select(ValidationResult).where(ValidationResult.validation_run_id == validation_run.id)
        )
    ]

    report_progress("Reviewing", 85, "Running the Reviewer Agent")
    reviewer_row = _new_reviewer_run(
        db, job, {"finding_id": finding_id, "validation_run_id": validation_run.id}
    )
    reviewer_outcome, verdict = run_reviewer_agent(
        finding=finding,
        change_plan=change_plan,
        diff_summary=_diff_summary(cms_result),
        actual_files=_urls(cms_result),
        validation_summary=validation_summary,
        gateway=gateway,
        budget=budget,
        dry_run=False,
        diff_files=_diff_files(cms_result),
    )
    _persist_agent_run(db, reviewer_row, reviewer_outcome, result_json=reviewer_outcome.output or {})

    approved = (
        verdict is not None
        and verdict.approved
        and validation_run.status is ValidationRunStatus.PASSED
    )
    change_set = None
    if approved:
        finding.status = FindingStatus.VALIDATED
        change_set = _record_change_set(
            db,
            finding=finding,
            cms_result=cms_result,
            validation_run=validation_run,
            seed=seed,
            snapshot=snapshot,
        )
    else:
        finding.status = FindingStatus.REJECTED
        if applied_live:
            connector.restore_snapshot_row(snapshot)
    db.commit()

    _persist_agent_run(
        db,
        seed,
        cms_result.outcome,
        result_json={
            "platform": WORDPRESS_PLATFORM,
            "live": live,
            "diff_preview": _diff_preview(cms_result),
            "validation_run_id": validation_run.id,
            "validation_status": validation_run.status.value,
            "reviewer_agent_run_id": reviewer_row.id,
            "reviewer_verdict": verdict.model_dump() if verdict is not None else None,
            "approved": approved,
            "change_set_id": change_set.id if change_set is not None else None,
            "snapshot_id": snapshot.id,
        },
    )
    report_progress("Finishing", 100, f"finding={finding.status.value} approved={approved}")


def _overlay_pages(connector: WordPressConnector, result: CmsAgentResult):
    if result.patch is None:
        return []
    pages = []
    for url in {item.url for item in result.patch.mutations}:
        page = connector.fetch_content(url)
        updates: dict = {}
        for item in result.patch.mutations:
            if item.url != url:
                continue
            if item.field in {"content"}:
                updates["content"] = item.new_value
            elif item.field in {"title", "seo_title"}:
                updates["title"] = item.new_value
            elif item.field == "meta_description":
                updates["meta_description"] = item.new_value
            elif item.field == "canonical":
                updates["canonical"] = item.new_value
        pages.append(page.model_copy(update=updates) if updates else page)
    return pages


def _urls(result: CmsAgentResult) -> list[str]:
    if result.patch is None:
        return []
    return [item.url for item in result.patch.mutations]


def _diff_files(result: CmsAgentResult) -> list[dict]:
    if result.patch is None:
        return []
    files = []
    for item in result.patch.mutations:
        before = (result.before.get(item.url) or {}).get(item.field, "")
        files.append(
            {
                "file_path": item.url,
                "change_summary": f"{item.field}: {item.change_summary}",
                "unified_diff": f"--- {item.url} {item.field}\n+++ {item.url} {item.field}\n-{before}\n+{item.new_value}\n",
            }
        )
    return files


def _diff_summary(result: CmsAgentResult) -> str:
    if result.patch is None:
        return result.error or ""
    return compose_diff_summary(notes=result.patch.notes, files=_diff_files(result))


def _diff_preview(result: CmsAgentResult) -> dict:
    if result.patch is None:
        return {"notes": result.error or "", "files": []}
    return {
        "notes": result.patch.notes,
        "files": [
            {
                "file_path": item.url,
                "change_summary": f"{item.field}: {item.change_summary}",
                "before": (result.before.get(item.url) or {}).get(item.field, ""),
                "after": item.new_value,
            }
            for item in result.patch.mutations
        ],
    }


def _load_intervention(
    db: Session, project_id: int, source_agent_run_id: int, finding_id: str
) -> Intervention | None:
    row = db.get(AgentRun, source_agent_run_id)
    if row is None or row.project_id != project_id or not isinstance(row.result_json, list):
        return None
    for item in row.result_json:
        if isinstance(item, dict) and item.get("finding_id") == finding_id:
            try:
                return Intervention.model_validate(item)
            except ValidationError:
                return None
    return None


def _record_change_set(
    db: Session,
    *,
    finding: Finding,
    cms_result: CmsAgentResult,
    validation_run,
    seed: AgentRun,
    snapshot,
) -> ChangeSet:
    assert cms_result.patch is not None
    change_set = create_change_set(
        db,
        project_id=finding.project_id,
        objective=finding.recommended_action,
        description=cms_result.patch.notes,
        finding_ids=[finding.finding_id],
        evidence=finding.evidence,
        affected_resources=[item.url for item in cms_result.patch.mutations],
        risk=finding.risk,
        validation_run_id=validation_run.id,
        rollback_info={
            "method": _ROLLBACK_METHOD,
            "snapshot_id": snapshot.id if snapshot is not None else None,
        },
    )
    for item in cms_result.patch.mutations:
        record_transaction(
            db,
            change_set_id=change_set.id,
            project_id=finding.project_id,
            agent_run_id=seed.id,
            snapshot_id=snapshot.id if snapshot is not None else None,
            finding_id=finding.finding_id,
            resource=item.url,
            content_before=(cms_result.before.get(item.url) or {}).get(item.field, ""),
            content_after=item.new_value,
            reason=finding.rule,
            evidence=finding.evidence,
            validation_status=validation_run.status,
            platform=ChangePlatform.WORDPRESS,
            field=item.field,
            rollback_method=_ROLLBACK_METHOD,
        )
    record_baseline_for_change_set(db, change_set, finding=finding, fetch_gsc=True)
    return change_set


def _new_reviewer_run(db: Session, job: Job, objective_json: dict) -> AgentRun:
    row = AgentRun(
        project_id=job.project_id,
        job_id=job.id,
        agent_type=AgentType.REVIEWER,
        status=AgentRunStatus.RUNNING,
        objective_json=objective_json,
        started_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _persist_agent_run(db: Session, row: AgentRun, outcome, *, result_json: dict) -> None:
    for index, message in enumerate(outcome.messages, start=1):
        db.add(
            AgentMessage(
                agent_run_id=row.id,
                seq=index,
                role=AgentMessageRole(message.role),
                content=message.content,
                tool_name=message.tool_name,
                provider=message.provider,
                model=message.model,
                tokens=message.tokens,
            )
        )
    row.status = outcome.status
    row.result_json = result_json
    row.iterations_used = outcome.iterations_used
    row.tool_calls_used = outcome.tool_calls_used
    row.tokens_used = outcome.tokens_used
    row.files_modified = outcome.files_modified
    row.stopped_reason = outcome.stopped_reason
    row.error = outcome.error
    row.finished_at = datetime.now(timezone.utc)
    db.commit()


register("cms_change", cms_change)
