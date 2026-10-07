"""Agent run job (step 6.1/6.4/6.5/6.7 verify).

Drives the full pipeline — Intent Planner -> Research Planner -> Research
Agent -> SEO Agent — through the real job queue/worker, with the LLM and
retrieval stubbed. Asserts: no file/CMS mutation happens (nothing in this
test touches the filesystem or any connector), every budget type is
enforceable, and `agent_runs`/`agent_messages` persist with token
accounting.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.jobs.queue import get_redis
from app.knowledge.authority import AuthorityLevel
from app.models.agent import AgentMessage, AgentRun, AgentRunStatus, AgentType
from app.models.finding import AnalysisRun, AnalysisRunStatus, Finding, FindingStatus
from app.models.job import Job, JobStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project
from app.models.user import User
from app.db.session import SessionLocal
from app.retrieval.hybrid import RetrievalResult
from app.worker import run_job

_FINDING_ID_RE = re.compile(r'"finding_id":\s*"([^"]+)"')


class _FakeChatResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.provider = "fake"
        self.model = "fake-small"
        self.tokens = 10
        self.latency_ms = 1


class _FakeGateway:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def chat(self, messages, *, tier=None, response_format=None, max_tokens=None):
        system = next(m["content"] for m in messages if m["role"] == "system")
        if "Intent Planner" in system:
            self._calls.append("intent")
            content = json.dumps(
                {
                    "objective": "SEO optimization",
                    "scope": "product pages",
                    "allowed_actions": ["metadata"],
                    "mode": "audit_and_fix",
                }
            )
        elif "Research Planner" in system:
            self._calls.append("research")
            content = json.dumps(
                {
                    "repository_needed": False,
                    "website_needed": False,
                    "search_console_needed": False,
                    "knowledge_needed": True,
                    "graph_needed": False,
                    "notes": "knowledge only",
                }
            )
        elif "Optimization Planner" in system:
            self._calls.append("optimization")
            joined = "\n".join(m["content"] for m in messages if m["role"] != "system")
            finding_ids = _FINDING_ID_RE.findall(joined) or ["unknown"]
            content = json.dumps(
                {
                    "items": [
                        {
                            "finding_id": finding_id,
                            "hypothesis": "h",
                            "intervention": "i",
                            "expected_mechanism": "m",
                            "risk": "low",
                        }
                        for finding_id in finding_ids
                    ]
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
        user = User(email=f"agent-test-{uuid.uuid4()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
        db.refresh(user)
    proj = Project(name=f"agent-test-{uuid.uuid4()}", created_by=user.id)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    yield proj
    db.rollback()
    # AgentMessage rows cascade-delete with their AgentRun (ondelete="CASCADE").
    for row in db.scalars(select(AgentRun).where(AgentRun.project_id == proj.id)):
        db.delete(row)
    for row in db.scalars(select(Job).where(Job.project_id == proj.id)):
        db.delete(row)
    for run in db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == proj.id)):
        db.delete(run)
    db.commit()
    row = db.get(Project, proj.id)
    if row is not None:
        db.delete(row)
        db.commit()


@pytest.fixture
def analysis_with_findings(db, project) -> AnalysisRun:
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
        affected_resource="https://example.com/product",
        expected_mechanism="Canonical tags prevent duplicate-content signals.",
        recommended_action="Add a canonical link tag.",
        recommendation="Add a canonical link tag.",
        actionability="recommend_only",
        risk="Low risk; metadata-only change.",
        will_validate="Re-crawl and confirm the canonical tag is present.",
        change_worked="not_yet_applied",
        rollback="Remove the added tag.",
        status=FindingStatus.OPEN,
    )
    db.add(finding)
    db.commit()
    return run


@pytest.fixture
def stub_retrieve(monkeypatch):
    def fake_retrieve(query: str, **_kwargs) -> RetrievalResult:
        return RetrievalResult(
            query=query,
            rewritten_query=query,
            rerank="fallback",
            rerank_reason="test",
            candidates_considered=0,
            items=[],
            llm_messages=[],
            gaps=[],
        )

    monkeypatch.setattr("app.agents.tools.retrieve", fake_retrieve)
    return fake_retrieve


@pytest.fixture
def stub_gateway(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "app.jobs.handlers.agent_run.LLMGateway", lambda *_a, **_kw: _FakeGateway(calls)
    )
    return calls


def _start_agent_run(db, project_id: int, request_text: str) -> Job:
    settings = get_settings()
    job = Job(project_id=project_id, type="agent_run", status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)

    seed = AgentRun(
        project_id=project_id,
        job_id=job.id,
        agent_type=AgentType.RESEARCH,
        status=AgentRunStatus.RUNNING,
        request_text=request_text,
    )
    db.add(seed)
    db.commit()

    get_redis(settings).lpush(settings.job_queue_key, str(job.id))
    return job


def _run_agent_job(db, project_id: int, request_text: str) -> Job:
    settings = get_settings()
    job = _start_agent_run(db, project_id, request_text)
    popped = get_redis(settings).brpop([settings.job_queue_key], timeout=5)
    assert popped is not None
    worker_db = SessionLocal()
    try:
        run_job(worker_db, job.id, settings)
    finally:
        worker_db.close()
    db.rollback()
    return db.get(Job, job.id)


def test_agent_run_produces_ranked_interventions_with_zero_mutation(
    db, project, analysis_with_findings, stub_retrieve, stub_gateway
) -> None:
    job = _run_agent_job(db, project.id, "Improve SEO for product pages")

    assert job.status is JobStatus.SUCCEEDED
    # High-confidence findings are templated from recorded fields — Layer 3
    # does not spend an LLM call rewriting recommended_action.
    assert stub_gateway == ["intent", "research"]

    runs = list(
        db.scalars(
            select(AgentRun).where(AgentRun.job_id == job.id).order_by(AgentRun.id.asc())
        )
    )
    agent_types = {row.agent_type for row in runs}
    assert AgentType.RESEARCH in agent_types
    assert AgentType.SEO in agent_types
    assert AgentType.AEO not in agent_types  # "Improve SEO..." only routes to SEO
    assert AgentType.GEO not in agent_types

    for row in runs:
        assert row.status is AgentRunStatus.SUCCEEDED
        assert row.finished_at is not None

    seo_run = next(row for row in runs if row.agent_type is AgentType.SEO)
    assert seo_run.result_json
    assert seo_run.result_json[0]["finding_id"] == "SEO-CANONICAL-001:abc123"
    assert seo_run.result_json[0]["intervention"] == "Add a canonical link tag."
    assert seo_run.tokens_used == 0

    messages = list(
        db.scalars(select(AgentMessage).where(AgentMessage.agent_run_id == seo_run.id))
    )
    assert messages
    assert messages[0].tokens == 0
    assert messages[0].provider is None


def test_agent_run_calls_optimizer_for_medium_confidence_findings(
    db, project, stub_retrieve, stub_gateway
) -> None:
    run = AnalysisRun(project_id=project.id, status=AnalysisRunStatus.SUCCEEDED)
    db.add(run)
    db.commit()
    db.refresh(run)
    db.add(
        Finding(
            project_id=project.id,
            analysis_run_id=run.id,
            finding_id="SEO-CANONICAL-001:med",
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
            confidence=RuleConfidence.MEDIUM,
            affected_resource="https://example.com/product",
            expected_mechanism="Canonical tags prevent duplicate-content signals.",
            recommended_action="Add a canonical link tag.",
            recommendation="Add a canonical link tag.",
            actionability="recommend_only",
            risk="Low risk; metadata-only change.",
            will_validate="Re-crawl and confirm the canonical tag is present.",
            change_worked="not_yet_applied",
            rollback="Remove the added tag.",
            status=FindingStatus.OPEN,
        )
    )
    db.commit()

    job = _run_agent_job(db, project.id, "Improve SEO for product pages")

    assert job.status is JobStatus.SUCCEEDED
    assert stub_gateway == ["intent", "research", "optimization"]
    seo_run = db.scalar(
        select(AgentRun).where(AgentRun.job_id == job.id, AgentRun.agent_type == AgentType.SEO)
    )
    assert seo_run is not None
    assert seo_run.status is AgentRunStatus.SUCCEEDED
    assert seo_run.result_json[0]["finding_id"] == "SEO-CANONICAL-001:med"
    assert seo_run.tokens_used > 0


def test_agent_run_iteration_budget_reports_partial_progress(
    db, project, analysis_with_findings, stub_retrieve, stub_gateway, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "0")
    from app.core.config import get_settings as _get_settings

    _get_settings.cache_clear()
    try:
        job = _run_agent_job(db, project.id, "Improve SEO for product pages")
        assert job.status is JobStatus.SUCCEEDED  # a budget stop is not a job failure

        runs = list(db.scalars(select(AgentRun).where(AgentRun.job_id == job.id)))
        assert runs
        for row in runs:
            assert row.status is AgentRunStatus.PARTIAL
            assert row.stopped_reason == "max_iterations"
    finally:
        monkeypatch.delenv("AGENT_MAX_ITERATIONS", raising=False)
        _get_settings.cache_clear()
