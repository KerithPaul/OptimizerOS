"""Changes API (step 7.10 verify): apply/list/detail over real HTTP.

Runs against the real MySQL/Redis dev containers, exercising the same
seed-row-before-enqueue transaction `app.api.v1.agents` already uses,
now for `AgentType.CODE`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.knowledge.authority import AuthorityLevel
from app.main import app
from app.models.agent import AgentMessage, AgentRun, AgentRunStatus, AgentType
from app.models.finding import AnalysisRun, Finding, FindingStatus
from app.models.job import Job
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project, ProjectMode


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    settings = get_settings()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": settings.seed_user_email, "password": settings.seed_user_password},
    )
    assert resp.status_code == 200
    return client


@pytest.fixture
def project_row(auth_client):
    resp = auth_client.post(
        "/api/v1/projects",
        json={"name": f"changes-api-test-{uuid.uuid4()}", "mode": "AUDIT_ONLY"},
    )
    assert resp.status_code == 201
    body = resp.json()
    yield body

    db = SessionLocal()
    try:
        for row in db.scalars(select(AgentMessage).where(AgentMessage.agent_run_id.in_(
            select(AgentRun.id).where(AgentRun.project_id == body["id"])
        ))):
            db.delete(row)
        for row in db.scalars(select(AgentRun).where(AgentRun.project_id == body["id"])):
            db.delete(row)
        for row in db.scalars(select(Job).where(Job.project_id == body["id"])):
            db.delete(row)
        for row in db.scalars(select(Finding).where(Finding.project_id == body["id"])):
            db.delete(row)
        for row in db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == body["id"])):
            db.delete(row)
        db.commit()
        proj = db.get(Project, body["id"])
        if proj is not None:
            db.delete(proj)
            db.commit()
    finally:
        db.close()


def _seed_finding(project_id: int, *, actionability: str, finding_id: str) -> Finding:
    db = SessionLocal()
    try:
        run = AnalysisRun(project_id=project_id, status="succeeded")
        db.add(run)
        db.commit()
        db.refresh(run)

        finding = Finding(
            project_id=project_id,
            analysis_run_id=run.id,
            finding_id=finding_id,
            observation="Missing canonical tag",
            problem="Missing canonical tag",
            evidence=[{"source": "page", "excerpt": "no canonical", "confidence": "direct"}],
            source="ArchitectOS rule catalog",
            source_url="https://example.com/rules/seo-canonical-001",
            source_authority=AuthorityLevel.OFFICIAL_STANDARD,
            rule="SEO-CANONICAL-001",
            rule_version=1,
            category=RuleCategory.TECHNICAL_SEO,
            severity=RuleSeverity.HIGH,
            confidence=RuleConfidence.HIGH,
            affected_resource="https://example.com/products/x",
            affected_code_entity="app/products/[slug]/page.tsx",
            expected_mechanism="Canonical tags prevent duplicate-content signals.",
            recommended_action="Add a canonical link tag.",
            recommendation="Add a canonical link tag.",
            actionability=actionability,
            risk="Low risk.",
            will_validate="Re-crawl.",
            change_worked="not_yet_applied",
            rollback="Remove the tag.",
            status=FindingStatus.OPEN,
        )
        db.add(finding)
        db.commit()
        db.refresh(finding)
        return finding
    finally:
        db.close()


def _seed_source_run(project_id: int, finding_id: str) -> AgentRun:
    db = SessionLocal()
    try:
        row = AgentRun(
            project_id=project_id,
            agent_type=AgentType.SEO,
            status=AgentRunStatus.SUCCEEDED,
            result_json=[
                {
                    "finding_id": finding_id,
                    "hypothesis": "h",
                    "intervention": "i",
                    "expected_mechanism": "m",
                    "risk": "low",
                }
            ],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def test_apply_rejects_recommend_only_finding(auth_client, project_row) -> None:
    finding = _seed_finding(
        project_row["id"], actionability="recommend_only", finding_id="SEO-X:1"
    )
    source_run = _seed_source_run(project_row["id"], finding.finding_id)

    resp = auth_client.post(
        f"/api/v1/projects/{project_row['id']}/changes/apply",
        json={"finding_id": finding.finding_id, "source_agent_run_id": source_run.id},
    )
    assert resp.status_code == 400
    assert "actionability" in resp.json()["detail"]


def test_apply_unknown_finding_is_404(auth_client, project_row) -> None:
    source_run = _seed_source_run(project_row["id"], "does-not-matter")
    resp = auth_client.post(
        f"/api/v1/projects/{project_row['id']}/changes/apply",
        json={"finding_id": "no-such-finding", "source_agent_run_id": source_run.id},
    )
    assert resp.status_code == 404


def test_apply_with_no_repository_is_rejected(auth_client, project_row) -> None:
    finding = _seed_finding(project_row["id"], actionability="code_change", finding_id="SEO-Y:1")
    source_run = _seed_source_run(project_row["id"], finding.finding_id)

    resp = auth_client.post(
        f"/api/v1/projects/{project_row['id']}/changes/apply",
        json={"finding_id": finding.finding_id, "source_agent_run_id": source_run.id},
    )
    assert resp.status_code == 400
    assert "repository" in resp.json()["detail"].lower()


def test_apply_in_audit_only_mode_is_a_dry_run(auth_client, project_row, monkeypatch) -> None:
    """AUDIT_ONLY has no repository requirement to reach the sandbox/apply
    path -- but it does need one to plan against, matching "applies only to
    projects with source." A repository row (even unindexed) is enough for
    a dry-run job to be queued; the job itself proves the diff-only path in
    `test_code_change_job.py`. Here we only verify the API's mode gate."""

    finding = _seed_finding(project_row["id"], actionability="code_change", finding_id="SEO-Z:1")
    source_run = _seed_source_run(project_row["id"], finding.finding_id)

    from app.models.repository import CloneStatus, Repository

    db = SessionLocal()
    try:
        repo = Repository(
            project_id=project_row["id"],
            url="https://example.invalid/repo.git",
            default_branch="main",
            clone_status=CloneStatus.PENDING,
        )
        db.add(repo)
        db.commit()
    finally:
        db.close()

    resp = auth_client.post(
        f"/api/v1/projects/{project_row['id']}/changes/apply",
        json={"finding_id": finding.finding_id, "source_agent_run_id": source_run.id},
    )
    assert resp.status_code == 201
    assert resp.json()["dry_run"] is True

    db = SessionLocal()
    try:
        job_id = resp.json()["job_id"]
        seed = db.scalar(
            select(AgentRun).where(AgentRun.job_id == job_id, AgentRun.agent_type == AgentType.CODE)
        )
        if seed is not None:
            db.delete(seed)
        job_row = db.get(Job, job_id)
        if job_row is not None:
            db.delete(job_row)
        repo_row = db.scalar(select(Repository).where(Repository.project_id == project_row["id"]))
        if repo_row is not None:
            db.delete(repo_row)
        db.commit()
    finally:
        db.close()


def test_detail_normalizes_empty_diff_preview(auth_client, project_row) -> None:
    """A locate/scope stop used to persist `diff_preview: {}`. That object is
    truthy in JS and crashed the change-review page on `.files.length`."""

    finding = _seed_finding(
        project_row["id"], actionability="code_change", finding_id="SEO-EMPTY-DIFF:1"
    )
    db = SessionLocal()
    try:
        row = AgentRun(
            project_id=project_row["id"],
            agent_type=AgentType.CODE,
            status=AgentRunStatus.PARTIAL,
            objective_json={"finding_id": finding.finding_id, "dry_run": True},
            result_json={
                "dry_run": True,
                "change_plan": {
                    "finding_id": finding.finding_id,
                    "target_files": ["app/page.tsx"],
                    "target_symbols": [],
                    "reuse_notes": "existing metadata helper",
                    "expected_diff_summary": "Add a canonical tag.",
                    "required_tests": [],
                    "required_validation": ["seo"],
                },
                "diff_preview": {},
                "violation": {"reason": "locate_miss", "detail": "locator not in target files"},
            },
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    resp = auth_client.get(
        f"/api/v1/projects/{project_row['id']}/changes/{finding.finding_id}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["diff_preview"] == {"notes": "", "files": []}
    assert body["change_plan"]["target_files"] == ["app/page.tsx"]
    assert body["violation"]["reason"] == "locate_miss"


def test_list_and_detail_for_unknown_change(auth_client, project_row) -> None:
    resp = auth_client.get(f"/api/v1/projects/{project_row['id']}/changes")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = auth_client.get(f"/api/v1/projects/{project_row['id']}/changes/no-such-finding")
    assert resp.status_code == 404
