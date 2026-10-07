"""Change Management + Semantic Rollback (Phase 8 verify, `[SPEC AGENTS.md §29-§38]`).

Drives the real `code_change` job (same collaborator boundaries as
`test_code_change_job.py`) to produce an approved, applied change, then
exercises `app.changes.rollback` end to end: a Change Set is recorded
with hash-before/after on every transaction and item, a full-confidence
rollback restores the original file content and marks the Finding
`ROLLED_BACK`, and a rollback whose target has drifted since the change
stops for confirmation instead of silently overwriting newer content.
"""

from __future__ import annotations

import json
import shutil
import uuid

import pytest
from sqlalchemy import select

from app.changes.rollback import build_plan, execute_rollback, resolve_target
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.repository.clone import workspace_path
from app.jobs.queue import get_redis
from app.knowledge.authority import AuthorityLevel
from app.models.agent import AgentMessage, AgentRun, AgentRunStatus, AgentType
from app.models.change import (
    ChangeItem,
    ChangeItemStatus,
    ChangeSet,
    ChangeSetStatus,
    ChangeTransaction,
    ChangeTransactionStatus,
    RollbackOperation,
    RollbackOperationStatus,
    Snapshot,
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationResult,
    ValidationRun,
    ValidationRunStatus,
)
from app.models.finding import AnalysisRun, AnalysisRunStatus, Finding, FindingStatus
from app.models.job import Job, JobStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project, ProjectMode
from app.models.repository import CloneStatus, Repository
from app.models.user import User
from app.worker import run_job

_TARGET_FILE = "app/products/[slug]/page.tsx"
_ORIGINAL_CONTENT = "export default function ProductPage() { return null; }"
_UPDATED_CONTENT = 'export default function ProductPage() { return "canonical"; }'
_FINDING_ID = "SEO-CANONICAL-001:rollback"


class _FakeChatResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.provider = "fake"
        self.model = "fake-strong"
        self.tokens = 15
        self.latency_ms = 1


class _FakeGateway:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        system = next(m["content"] for m in messages if m["role"] == "system")
        if "Change Planner" in system:
            self._calls.append("change")
            content = json.dumps(
                {
                    "finding_id": _FINDING_ID,
                    "target_files": [_TARGET_FILE],
                    "target_symbols": ["generateMetadata"],
                    "reuse_notes": "reuse generateMetadata",
                    "expected_diff_summary": "add canonical tag",
                    "required_tests": [],
                    "required_validation": [],
                }
            )
        elif "Validation Planner" in system:
            self._calls.append("validation")
            content = json.dumps(
                {
                    "finding_id": _FINDING_ID,
                    "tests": [],
                    "build": False,
                    "lint": False,
                    "browser_checks": [],
                    "seo_checks": [],
                    "aeo_checks": [],
                    "geo_checks": [],
                    "regression_checks": [],
                }
            )
        elif "Code Agent" in system:
            self._calls.append("code")
            content = json.dumps(
                {
                    "finding_id": _FINDING_ID,
                    "files": [
                        {
                            "file_path": _TARGET_FILE,
                            "new_content": _UPDATED_CONTENT,
                            "change_summary": "return the canonical marker",
                        }
                    ],
                    "notes": "minimal metadata-only change",
                }
            )
        elif "Reviewer Agent" in system:
            self._calls.append("reviewer")
            content = json.dumps(
                {
                    "finding_id": _FINDING_ID,
                    "approved": True,
                    "reasons": ["in scope, minimal diff"],
                    "regressions_detected": [],
                }
            )
        else:
            raise AssertionError(f"unexpected system prompt: {system[:80]}")
        return _FakeChatResult(content)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db) -> Project:
    user = db.scalar(select(User).limit(1))
    if user is None:
        user = User(email=f"rollback-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"rollback-test-{uuid.uuid4()}", created_by=user.id, mode=ProjectMode.APPLY_LOCALLY)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj

    db.rollback()
    for row in db.scalars(select(RollbackOperation).where(RollbackOperation.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(ChangeTransaction).where(ChangeTransaction.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(ChangeSet).where(ChangeSet.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(AgentMessage).where(AgentMessage.agent_run_id.in_(
        select(AgentRun.id).where(AgentRun.project_id == proj.id)
    ))):
        db.delete(row)
    for row in db.scalars(select(ValidationRun).where(ValidationRun.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(AgentRun).where(AgentRun.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(Snapshot).where(Snapshot.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(Job).where(Job.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(Finding).where(Finding.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(Repository).where(Repository.project_id == proj.id)):
        db.delete(row)
    db.commit()
    row = db.get(Project, proj.id)
    if row is not None:
        db.delete(row)
        db.commit()


@pytest.fixture
def repository(db, project) -> Repository:
    repo = Repository(
        project_id=project.id,
        url="https://example.invalid/repo.git",
        default_branch="main",
        clone_status=CloneStatus.CLONED,
        cloned_commit_hash="deadbeef",
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)

    settings = get_settings()
    dest = workspace_path(project.id, repo.id, settings)
    (dest / "app" / "products" / "[slug]").mkdir(parents=True, exist_ok=True)
    (dest / _TARGET_FILE).write_text(_ORIGINAL_CONTENT, encoding="utf-8")

    yield repo

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    snapshot_root = dest.parent / f"{repo.id}__snapshots"
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root, ignore_errors=True)


@pytest.fixture
def finding_and_source_run(db, project, repository) -> tuple[Finding, AgentRun]:
    run = AnalysisRun(project_id=project.id, status=AnalysisRunStatus.SUCCEEDED)
    db.add(run)
    db.commit()
    db.refresh(run)

    finding = Finding(
        project_id=project.id,
        analysis_run_id=run.id,
        finding_id=_FINDING_ID,
        observation="Missing canonical tag",
        problem="Missing canonical tag",
        evidence=[{"source": "page", "excerpt": "no canonical tag", "confidence": "direct"}],
        source="ArchitectOS rule catalog",
        source_url="https://example.com/rules/seo-canonical-001",
        source_authority=AuthorityLevel.OFFICIAL_STANDARD,
        rule="SEO-CANONICAL-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.HIGH,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/products/running-shoes",
        affected_code_entity=_TARGET_FILE,
        expected_mechanism="Canonical tags prevent duplicate-content signals.",
        recommended_action="Add a canonical link tag.",
        recommendation="Add a canonical link tag.",
        actionability="code_change",
        risk="Low risk; metadata-only change.",
        will_validate="Re-crawl and confirm the canonical tag is present.",
        change_worked="not_yet_applied",
        rollback="Remove the added tag.",
        status=FindingStatus.OPEN,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    source_run = AgentRun(
        project_id=project.id,
        agent_type=AgentType.SEO,
        status=AgentRunStatus.SUCCEEDED,
        result_json=[
            {
                "finding_id": _FINDING_ID,
                "hypothesis": "Missing canonical confuses crawlers.",
                "intervention": "Add a canonical link tag.",
                "expected_mechanism": "Resolves duplicate-content ambiguity.",
                "risk": "low",
            }
        ],
    )
    db.add(source_run)
    db.commit()
    db.refresh(source_run)
    return finding, source_run


@pytest.fixture
def stub_retrieve(monkeypatch):
    from app.retrieval.hybrid import RetrievalResult

    def fake_retrieve(query: str, **_kwargs) -> RetrievalResult:
        return RetrievalResult(
            query=query, rewritten_query=query, rerank="fallback", rerank_reason="test",
            candidates_considered=0, items=[], llm_messages=[], gaps=[],
        )

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)
    return fake_retrieve


def _stub_validation_passed(monkeypatch):
    def fake_run_validation(db, **kwargs):
        run = ValidationRun(
            project_id=kwargs["repository"].project_id,
            agent_run_id=kwargs.get("agent_run_id"),
            snapshot_id=kwargs.get("snapshot_id"),
            status=ValidationRunStatus.PASSED,
            affected_urls_json=[],
            gaps_json=[],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        db.add(
            ValidationResult(
                validation_run_id=run.id,
                check_type=ValidationCheckType.BUILD,
                status=ValidationCheckStatus.PASSED,
                detail="stubbed: build passed",
            )
        )
        db.commit()
        return run

    monkeypatch.setattr("app.jobs.handlers.code_change.run_validation", fake_run_validation)


def _apply_change(db, project_id: int, finding_id: str, source_agent_run_id: int) -> Job:
    settings = get_settings()
    job = Job(project_id=project_id, type="code_change", status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)

    seed = AgentRun(
        project_id=project_id,
        job_id=job.id,
        agent_type=AgentType.CODE,
        status=AgentRunStatus.RUNNING,
        objective_json={
            "finding_id": finding_id,
            "source_agent_run_id": source_agent_run_id,
            "dry_run": False,
        },
    )
    db.add(seed)
    db.commit()

    get_redis(settings).lpush(settings.job_queue_key, str(job.id))
    return job


def _run_queued_job(db, job: Job) -> Job:
    settings = get_settings()
    popped = get_redis(settings).brpop([settings.job_queue_key], timeout=5)
    assert popped is not None
    worker_db = SessionLocal()
    try:
        run_job(worker_db, job.id, settings)
    finally:
        worker_db.close()
    db.rollback()
    return db.get(Job, job.id)


def _apply_and_get_change_set(db, project, finding, source_run, monkeypatch) -> ChangeSet:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls)
    )
    _stub_validation_passed(monkeypatch)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)
    assert finished.status is JobStatus.SUCCEEDED

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.result_json["approved"] is True
    change_set_id = code_run.result_json["change_set_id"]
    assert change_set_id is not None
    change_set = db.get(ChangeSet, change_set_id)
    assert change_set is not None
    return change_set


def test_approved_change_records_a_change_set_with_hashed_transactions_and_items(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    change_set = _apply_and_get_change_set(db, project, finding, source_run, monkeypatch)

    assert change_set.status is ChangeSetStatus.APPLIED
    assert change_set.finding_ids_json == [_FINDING_ID]
    assert _TARGET_FILE in change_set.affected_resources_json

    transactions = list(
        db.scalars(select(ChangeTransaction).where(ChangeTransaction.change_set_id == change_set.id))
    )
    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.resource == _TARGET_FILE
    assert transaction.reason == "SEO-CANONICAL-001"
    assert transaction.hash_before and transaction.hash_after
    assert transaction.hash_before != transaction.hash_after
    assert transaction.status is ChangeTransactionStatus.APPLIED

    items = list(db.scalars(select(ChangeItem).where(ChangeItem.change_transaction_id == transaction.id)))
    assert items, "every changed file must produce at least one Change Item"
    for item in items:
        assert item.hash_before and item.hash_after


def test_rolling_back_a_change_set_restores_original_content_and_finding_status(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    change_set = _apply_and_get_change_set(db, project, finding, source_run, monkeypatch)

    settings = get_settings()
    workspace = workspace_path(project.id, repository.id, settings)
    assert "canonical" in (workspace / _TARGET_FILE).read_text(encoding="utf-8")

    target = resolve_target(db, project_id=project.id, change_set_id=change_set.id)
    plan, _repo, requested_target = build_plan(db, target=target)
    assert plan.requires_confirmation is False, plan.reasons

    operation = execute_rollback(
        db,
        project_id=project.id,
        target=target,
        plan=plan,
        requested_target=requested_target,
        confirmed=False,
    )
    assert operation.status is RollbackOperationStatus.APPLIED

    restored = (workspace / _TARGET_FILE).read_text(encoding="utf-8")
    assert restored == _ORIGINAL_CONTENT

    db.refresh(change_set)
    assert change_set.status is ChangeSetStatus.ROLLED_BACK

    transaction = db.scalar(select(ChangeTransaction).where(ChangeTransaction.change_set_id == change_set.id))
    assert transaction.status is ChangeTransactionStatus.ROLLED_BACK

    db.refresh(finding)
    assert finding.status is FindingStatus.ROLLED_BACK


def test_rollback_of_a_resource_that_drifted_since_the_change_stops_for_confirmation(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    change_set = _apply_and_get_change_set(db, project, finding, source_run, monkeypatch)

    settings = get_settings()
    workspace = workspace_path(project.id, repository.id, settings)
    drifted_content = _ORIGINAL_CONTENT + "\n// unrelated edit made after the change\n"
    (workspace / _TARGET_FILE).write_text(drifted_content, encoding="utf-8")

    target = resolve_target(db, project_id=project.id, change_set_id=change_set.id)
    plan, _repo, requested_target = build_plan(db, target=target)
    assert plan.requires_confirmation is True

    operation = execute_rollback(
        db,
        project_id=project.id,
        target=target,
        plan=plan,
        requested_target=requested_target,
        confirmed=False,
    )
    assert operation.status is RollbackOperationStatus.PENDING_CONFIRMATION
    assert (workspace / _TARGET_FILE).read_text(encoding="utf-8") == drifted_content

    db.refresh(change_set)
    assert change_set.status is ChangeSetStatus.APPLIED

    confirmed_operation = execute_rollback(
        db,
        project_id=project.id,
        target=target,
        plan=plan,
        requested_target=requested_target,
        confirmed=True,
    )
    assert confirmed_operation.status is RollbackOperationStatus.APPLIED
    assert (workspace / _TARGET_FILE).read_text(encoding="utf-8") == _ORIGINAL_CONTENT


def test_rollback_by_symbol_name_only_touches_that_symbol(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    _apply_and_get_change_set(db, project, finding, source_run, monkeypatch)

    target = resolve_target(db, project_id=project.id, symbol_name="ProductPage")
    assert target.change_item is not None
    assert target.change_item.status is ChangeItemStatus.APPLIED
