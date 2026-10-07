"""Code change job (step 7.1-7.8 verify, `[SPEC IMPLEMENTATION_PLAN_V2.md §13]`).

Drives the full pipeline -- Layer 4/5/6 planners -> snapshot -> Code Agent
-> scope gate -> validation -> Reviewer Agent -- through the real job
queue/worker, with the LLM, sandbox/validation, and retrieval stubbed at
the same collaborator boundaries `test_agent_run_job.py` already uses.
Asserts: a snapshot always exists before a write, a scope violation
writes zero bytes and does not fail the job, `AUDIT_ONLY` never touches
disk, and an approved change lands as `FindingStatus.VALIDATED` while a
rejected one is reverted to its snapshot.
"""

from __future__ import annotations

import json
import shutil
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.intelligence.repository.clone import workspace_path
from app.jobs.queue import get_redis
from app.knowledge.authority import AuthorityLevel
from app.models.agent import AgentMessage, AgentRun, AgentRunStatus, AgentType
from app.connectors.github.publish import PublishError, PublishResult
from app.models.change import (
    Snapshot,
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationResult,
    ValidationRun,
    ValidationRunStatus,
)
from app.models.github import CommitStatus, GithubCommit
from app.models.finding import AnalysisRun, AnalysisRunStatus, Finding, FindingStatus
from app.models.job import Job, JobStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project, ProjectMode
from app.models.repository import CloneStatus, Repository
from app.models.user import User
from app.worker import run_job

_TARGET_FILE = "app/products/[slug]/page.tsx"
_ORIGINAL_CONTENT = "export default function ProductPage() { return null; }"


class _FakeChatResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.provider = "fake"
        self.model = "fake-strong"
        self.tokens = 15
        self.latency_ms = 1


class _FakeGateway:
    def __init__(self, calls: list[str], *, patch_file: str = _TARGET_FILE, approved: bool = True) -> None:
        self._calls = calls
        self._patch_file = patch_file
        self._approved = approved

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        system = next(m["content"] for m in messages if m["role"] == "system")
        if "Change Planner" in system:
            self._calls.append("change")
            content = json.dumps(
                {
                    "finding_id": "SEO-CANONICAL-001:abc123",
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
                    "finding_id": "SEO-CANONICAL-001:abc123",
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
                    "finding_id": "SEO-CANONICAL-001:abc123",
                    "files": [
                        {
                            "file_path": self._patch_file,
                            "new_content": _ORIGINAL_CONTENT + "\nexport const canonical = true;",
                            "change_summary": "added canonical export",
                        }
                    ],
                    "notes": "minimal metadata-only change",
                }
            )
        elif "Reviewer Agent" in system:
            self._calls.append("reviewer")
            content = json.dumps(
                {
                    "finding_id": "SEO-CANONICAL-001:abc123",
                    "approved": self._approved,
                    "reasons": ["in scope, minimal diff"] if self._approved else ["rejected by test"],
                    "regressions_detected": [],
                }
            )
        else:
            raise AssertionError(f"unexpected system prompt: {system[:80]}")
        return _FakeChatResult(content)


@pytest.fixture(autouse=True)
def _ignore_shared_heavy_slot(monkeypatch):
    """These tests call `run_job` in-process. A live worker's running heavy
    job on the shared Redis/MySQL stack must not refuse them.
    """
    monkeypatch.setattr("app.worker._try_acquire_heavy_slot", lambda _settings: True)
    monkeypatch.setattr("app.worker._release_heavy_slot", lambda _settings: None)


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
        user = User(email=f"code-change-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"code-change-test-{uuid.uuid4()}", created_by=user.id, mode=ProjectMode.APPLY_LOCALLY)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj

    db.rollback()
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
    (dest / "auth").mkdir(parents=True, exist_ok=True)
    (dest / "auth" / "login.ts").write_text("export function login() {}", encoding="utf-8")

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
        finding_id="SEO-CANONICAL-001:abc123",
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
                "finding_id": "SEO-CANONICAL-001:abc123",
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


def _stub_validation_passed(monkeypatch, calls: list[str]):
    def fake_run_validation(db, **kwargs):
        calls.append("validation")
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
        from app.models.change import ValidationResult

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
    return job


def _run_queued_job(db, job: Job) -> Job:
    settings = get_settings()
    from app.jobs.queue import HEAVY_JOB_COUNTER_KEY

    redis = get_redis(settings)
    redis.set(HEAVY_JOB_COUNTER_KEY, 0)
    while redis.lpop(settings.job_queue_key):
        pass
    worker_db = SessionLocal()
    try:
        run_job(worker_db, job.id, settings)
    finally:
        worker_db.close()
    db.rollback()
    return db.get(Job, job.id)


def test_approved_patch_is_written_and_finding_becomes_validated(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls, approved=True)
    )
    _stub_validation_passed(monkeypatch, calls)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)

    assert finished.status is JobStatus.SUCCEEDED
    assert calls == ["change", "validation", "code", "validation", "reviewer"]

    settings = get_settings()
    workspace = workspace_path(project.id, repository.id, settings)
    written = (workspace / _TARGET_FILE).read_text(encoding="utf-8")
    assert "export const canonical = true;" in written

    db.refresh(finding)
    assert finding.status is FindingStatus.VALIDATED

    snapshot = db.scalar(select(Snapshot).where(Snapshot.project_id == project.id))
    assert snapshot is not None, "a snapshot must exist before any modification"

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.status is AgentRunStatus.SUCCEEDED
    assert code_run.files_modified == 1
    assert code_run.result_json["approved"] is True

    reviewer_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.REVIEWER)
    )
    assert reviewer_run is not None
    assert reviewer_run.result_json["approved"] is True


def test_scope_violation_writes_nothing_and_rejects_the_finding(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway",
        lambda *_a, **_kw: _FakeGateway(calls, patch_file="auth/login.ts", approved=True),
    )

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)

    assert finished.status is JobStatus.SUCCEEDED, "a scope stop is a handled outcome, not a job failure"
    assert "reviewer" not in calls, "the Reviewer Agent must not run when there is nothing to review"

    settings = get_settings()
    workspace = workspace_path(project.id, repository.id, settings)
    assert (workspace / "auth" / "login.ts").read_text(encoding="utf-8") == "export function login() {}"
    assert (workspace / _TARGET_FILE).read_text(encoding="utf-8") == _ORIGINAL_CONTENT

    db.refresh(finding)
    assert finding.status is FindingStatus.REJECTED

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.status is AgentRunStatus.PARTIAL
    assert code_run.stopped_reason == "forbidden_directory"
    assert code_run.files_modified == 0
    assert code_run.result_json["validation_skipped"] is True
    assert code_run.result_json["reviewer_skipped"] is True
    assert code_run.result_json["approved"] is False
    validation_run_id = code_run.result_json["validation_run_id"]
    vrun = db.get(ValidationRun, validation_run_id)
    assert vrun is not None
    assert vrun.status is ValidationRunStatus.FAILED
    results = list(
        db.scalars(select(ValidationResult).where(ValidationResult.validation_run_id == vrun.id))
    )
    by_type = {row.check_type: row.status for row in results}
    assert by_type[ValidationCheckType.SCOPE] is ValidationCheckStatus.FAILED
    assert by_type[ValidationCheckType.BUILD] is ValidationCheckStatus.SKIPPED
    assert by_type[ValidationCheckType.LINT] is ValidationCheckStatus.SKIPPED


def test_code_agent_llm_exhaustion_reopens_finding_without_crashing_job(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    """Every LLM provider can fail on the Code Agent's own generation step
    (rate limits, a 5xx, a response that fails schema validation) after the
    planners already succeeded. `AgentRuntime.run` is documented to catch
    that and return a FAILED outcome rather than raise -- `code_change.py`
    must treat `code_result.patch is None` (no scope violation, just no
    patch) as a handled stop, not fall through to an `assert` that crashes
    the job and leaves the finding stuck at IN_PROGRESS forever."""

    class _CodeStepFailsGateway(_FakeGateway):
        def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
            system = next(m["content"] for m in messages if m["role"] == "system")
            if "Code Agent" in system:
                self._calls.append("code")
                raise RuntimeError("all providers exhausted: 503, invalid json, 403")
            return super().chat(messages, tier=tier, response_format=response_format, max_tokens=max_tokens)

    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _CodeStepFailsGateway(calls)
    )

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)

    assert finished.status is JobStatus.SUCCEEDED, "an exhausted-provider stop is a handled outcome, not a job crash"
    assert "reviewer" not in calls, "the Reviewer Agent must not run when there is no patch to review"

    settings = get_settings()
    workspace = workspace_path(project.id, repository.id, settings)
    assert (workspace / _TARGET_FILE).read_text(encoding="utf-8") == _ORIGINAL_CONTENT

    db.refresh(finding)
    assert finding.status is FindingStatus.OPEN

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.status is AgentRunStatus.FAILED
    assert "all providers exhausted" in (code_run.error or "")
    assert code_run.files_modified == 0


def test_apply_locally_does_not_publish_to_github(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls, approved=True)
    )
    _stub_validation_passed(monkeypatch, calls)

    def _must_not_publish(*_a, **_kw):
        raise AssertionError("APPLY_LOCALLY must not call GitHub publish")

    monkeypatch.setattr("app.jobs.handlers.code_change.publish_change_set", _must_not_publish)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)
    assert finished.status is JobStatus.SUCCEEDED
    db.refresh(finding)
    assert finding.status is FindingStatus.VALIDATED


def test_create_pr_after_reviewer_pass_publishes_once(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    project.mode = ProjectMode.CREATE_PR
    db.commit()
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls, approved=True)
    )
    _stub_validation_passed(monkeypatch, calls)

    published: list[int] = []

    def fake_publish(db, *, project, repository, change_set, job_id=None, rest_factory=None):
        published.append(change_set.id)
        commit = GithubCommit(
            project_id=project.id,
            repository_id=repository.id,
            change_set_id=change_set.id,
            job_id=job_id,
            sha="abc123",
            branch=f"architectos/{change_set.id}-add-a-canonical-link-tag",
            message=change_set.objective,
            files_json=list(change_set.affected_resources_json),
            status=CommitStatus.PUSHED,
        )
        db.add(commit)
        db.commit()
        db.refresh(commit)
        change_set.git_commit_ref = commit.sha
        change_set.pull_request_ref = "https://github.com/acme/shop/pull/7"
        db.commit()
        return PublishResult(commit=commit, pull_request=None)

    monkeypatch.setattr("app.jobs.handlers.code_change.publish_change_set", fake_publish)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)
    assert finished.status is JobStatus.SUCCEEDED
    assert len(published) == 1

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.result_json["github"]["sha"] == "abc123"
    assert "ghp_" not in str(code_run.result_json)


def test_create_pr_failure_fails_the_job_explicitly(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    project.mode = ProjectMode.CREATE_PR
    db.commit()
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls, approved=True)
    )
    _stub_validation_passed(monkeypatch, calls)

    def fake_publish(*_a, **_kw):
        raise PublishError("GitHub API 422: Validation Failed")

    monkeypatch.setattr("app.jobs.handlers.code_change.publish_change_set", fake_publish)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)
    assert finished.status is JobStatus.FAILED
    assert "GitHub PR failure" in (finished.error or "")

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.result_json["github"]["failed"] is True


def test_audit_only_mode_never_touches_disk(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    project.mode = ProjectMode.AUDIT_ONLY
    db.commit()
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls, approved=True)
    )

    settings = get_settings()
    job = Job(project_id=project.id, type="code_change", status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)
    seed = AgentRun(
        project_id=project.id,
        job_id=job.id,
        agent_type=AgentType.CODE,
        status=AgentRunStatus.RUNNING,
        objective_json={
            "finding_id": finding.finding_id,
            "source_agent_run_id": source_run.id,
            "dry_run": True,
        },
    )
    db.add(seed)
    db.commit()
    get_redis(settings).lpush(settings.job_queue_key, str(job.id))

    finished = _run_queued_job(db, job)

    assert finished.status is JobStatus.SUCCEEDED
    assert calls == ["change", "validation", "code", "reviewer"]

    workspace = workspace_path(project.id, repository.id, settings)
    assert (workspace / _TARGET_FILE).read_text(encoding="utf-8") == _ORIGINAL_CONTENT
    assert db.scalar(select(Snapshot).where(Snapshot.project_id == project.id)) is None

    db.refresh(finding)
    assert finding.status is FindingStatus.PLANNED

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.result_json["dry_run"] is True
    assert code_run.result_json["validation_skipped"] is True
    assert code_run.result_json["approved"] is None
    assert code_run.result_json["reviewer_verdict"]["approved"] is True
    assert code_run.result_json["diff_preview"]["files"][0]["file_path"] == _TARGET_FILE
    assert "before" in code_run.result_json["diff_preview"]["files"][0]

    reviewer_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.REVIEWER)
    )
    assert reviewer_run is not None


def test_planner_failure_marks_code_run_failed_not_running(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    """A Layer 6 schema miss must fail the job *and* the seed Code Agent run.

    The production bug left `agent_runs.status=running` after the worker
    marked the job failed, so the changes UI polled forever. Layer 5 is
    deterministic; Layer 6 is the remaining planner that can still
    return malformed JSON.
    """

    class _BrokenValidationGateway:
        def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
            system = next(m["content"] for m in messages if m["role"] == "system")
            if "Change Planner" in system:
                content = json.dumps(
                    {
                        "finding_id": "SEO-CANONICAL-001:abc123",
                        "target_files": [_TARGET_FILE],
                        "target_symbols": ["generateMetadata"],
                        "reuse_notes": "reuse generateMetadata",
                        "expected_diff_summary": "add canonical tag",
                        "required_tests": [],
                        "required_validation": [],
                    }
                )
            elif "Validation Planner" in system:
                content = '{"finding_id": "SEO-CANONICAL-001:abc123"}'
            else:
                raise AssertionError(f"unexpected system prompt: {system[:80]}")
            return _FakeChatResult(content)

    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway",
        lambda *_a, **_kw: _BrokenValidationGateway(),
    )

    finding, source_run = finding_and_source_run
    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)

    assert finished.status is JobStatus.FAILED
    assert "required schema" in (finished.error or "")

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.status is AgentRunStatus.FAILED
    assert code_run.stopped_reason == "job_failed"
    assert code_run.finished_at is not None
    assert "required schema" in (code_run.error or "")

    db.refresh(finding)
    assert finding.status is FindingStatus.OPEN


def test_failed_build_gets_one_repair_round_and_is_then_approved(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    code_prompts: list[list[dict]] = []

    class _RecordingGateway(_FakeGateway):
        def chat(self, messages, **kwargs):
            if "Code Agent" in next(m["content"] for m in messages if m["role"] == "system"):
                code_prompts.append(list(messages))
            return super().chat(messages, **kwargs)

    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _RecordingGateway(calls, approved=True)
    )

    attempts = {"n": 0}

    def fake_run_validation(db_, **kwargs):
        attempts["n"] += 1
        calls.append("run_validation")
        build_ok = attempts["n"] > 1
        run = ValidationRun(
            project_id=kwargs["repository"].project_id,
            agent_run_id=kwargs.get("agent_run_id"),
            snapshot_id=kwargs.get("snapshot_id"),
            status=ValidationRunStatus.PASSED if build_ok else ValidationRunStatus.FAILED,
            affected_urls_json=[],
            gaps_json=[],
        )
        db_.add(run)
        db_.commit()
        db_.refresh(run)
        db_.add(
            ValidationResult(
                validation_run_id=run.id,
                check_type=ValidationCheckType.BUILD,
                status=ValidationCheckStatus.PASSED if build_ok else ValidationCheckStatus.FAILED,
                detail="ok" if build_ok else "Type error: Cannot find module '../types'",
            )
        )
        db_.commit()
        return run

    monkeypatch.setattr("app.jobs.handlers.code_change.run_validation", fake_run_validation)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    finished = _run_queued_job(db, job)

    assert finished.status is JobStatus.SUCCEEDED
    assert calls == [
        "change", "validation", "code", "run_validation", "code", "run_validation", "reviewer",
    ]
    assert len(code_prompts) == 2
    assert "build FAILED" not in json.dumps(code_prompts[0])
    assert "Cannot find module '../types'" in code_prompts[1][-1]["content"]

    code_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.CODE)
    )
    assert code_run.result_json["repair_attempts"] == 1
    assert code_run.result_json["approved"] is True
    db.refresh(finding)
    assert finding.status is FindingStatus.VALIDATED


def test_build_that_keeps_failing_is_rolled_back_after_the_repair_round(
    db, project, repository, finding_and_source_run, stub_retrieve, monkeypatch
) -> None:
    finding, source_run = finding_and_source_run
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.code_change.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls, approved=False)
    )

    def always_failing_build(db_, **kwargs):
        calls.append("run_validation")
        run = ValidationRun(
            project_id=kwargs["repository"].project_id,
            agent_run_id=kwargs.get("agent_run_id"),
            snapshot_id=kwargs.get("snapshot_id"),
            status=ValidationRunStatus.FAILED,
            affected_urls_json=[],
            gaps_json=[],
        )
        db_.add(run)
        db_.commit()
        db_.refresh(run)
        db_.add(
            ValidationResult(
                validation_run_id=run.id,
                check_type=ValidationCheckType.BUILD,
                status=ValidationCheckStatus.FAILED,
                detail="Type error: still broken",
            )
        )
        db_.commit()
        return run

    monkeypatch.setattr("app.jobs.handlers.code_change.run_validation", always_failing_build)

    job = _apply_change(db, project.id, finding.finding_id, source_run.id)
    _run_queued_job(db, job)

    assert calls.count("code") == 2
    assert calls.count("run_validation") == 2
    workspace = workspace_path(project.id, repository.id, get_settings())
    assert (workspace / _TARGET_FILE).read_text(encoding="utf-8") == _ORIGINAL_CONTENT
    db.refresh(finding)
    assert finding.status is FindingStatus.REJECTED
