"""`analysis_runs` / `findings` record shape (step 5.B.1 / 5.B.2 verify)."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.knowledge.authority import AuthorityLevel
from app.models.finding import AnalysisRun, AnalysisRunStatus, Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project
from app.models.user import User
from app.retrieval.evidence import (
    EmptyEvidenceError,
    EvidenceConfidence,
    EvidenceRow,
    FindingRecord,
    persist_findings,
)


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
        user = User(email=f"findings-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"findings-test-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj
    db.rollback()
    row = db.get(Project, proj.id)
    if row is not None:
        db.delete(row)
        db.commit()


def _record() -> FindingRecord:
    return FindingRecord(
        finding_id="SEO-CANONICAL-001:deadbeefdeadbeef",
        observation="canonical missing on https://example.com/",
        evidence=[
            EvidenceRow(
                source="https://example.com/",
                excerpt="canonical is null",
                selector="canonical",
                value=None,
                confidence=EvidenceConfidence.DIRECT,
                source_authority="official_vendor_docs",
            )
        ],
        source="Google Search Central",
        source_url="https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
        source_authority=AuthorityLevel.OFFICIAL_VENDOR_DOCS,
        rule="SEO-CANONICAL-001",
        rule_version=1,
        category=RuleCategory.TECHNICAL_SEO,
        severity=RuleSeverity.MEDIUM,
        confidence=RuleConfidence.HIGH,
        affected_resource="https://example.com/",
        affected_url="https://example.com/",
        affected_code_entity=None,
        expected_mechanism="A canonical URL consolidates signals onto one preferred URL.",
        recommended_action="Add a self-referencing rel=canonical.",
        recommendation="Add a self-referencing rel=canonical.",
        problem="canonical missing on https://example.com/",
        actionability="recommend_only",
        risk="No change has been applied.",
        will_validate="Re-evaluate SEO-CANONICAL-001 against https://example.com/.",
        change_worked="not_yet_applied",
        rollback="No change has been applied. Rollback is not applicable until a Change Transaction exists.",
        status=FindingStatus.OPEN,
    )


def test_analysis_run_and_finding_can_be_inserted(db, project) -> None:
    run = AnalysisRun(project_id=project.id, status=AnalysisRunStatus.RUNNING)
    db.add(run)
    db.commit()
    db.refresh(run)
    persist_findings(db, run, [_record()])
    db.commit()
    stored = db.scalar(select(Finding).where(Finding.analysis_run_id == run.id))
    assert stored is not None
    assert stored.rule == "SEO-CANONICAL-001"
    assert stored.evidence
    assert stored.status is FindingStatus.OPEN
    assert stored.source_authority is AuthorityLevel.OFFICIAL_VENDOR_DOCS


def test_empty_evidence_cannot_be_persisted(db, project) -> None:
    run = AnalysisRun(project_id=project.id, status=AnalysisRunStatus.RUNNING)
    db.add(run)
    db.commit()
    db.refresh(run)
    payload = _record().model_dump()
    payload["evidence"] = []
    constructed = FindingRecord.model_construct(**payload)
    with pytest.raises(EmptyEvidenceError):
        persist_findings(db, run, [constructed])
    db.rollback()
    assert db.scalar(select(Finding).where(Finding.analysis_run_id == run.id)) is None


def test_finding_id_is_unique_per_analysis_run(db, project) -> None:
    run = AnalysisRun(project_id=project.id, status=AnalysisRunStatus.RUNNING)
    db.add(run)
    db.commit()
    db.refresh(run)
    persist_findings(db, run, [_record()])
    db.commit()
    with pytest.raises(IntegrityError):
        persist_findings(db, run, [_record()])
        db.commit()
    db.rollback()
