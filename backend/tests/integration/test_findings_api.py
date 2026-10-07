"""Analysis runs and findings API (step 5.C.3 verify): the drill-down path
in 5.C.1 works end to end — a score's signal names a rule, whose evidence
resolves back through the findings endpoint.
"""

import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.knowledge.authority import AuthorityLevel
from app.main import app
from app.models.finding import AnalysisRun, AnalysisRunStatus, Finding, FindingStatus
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity
from app.models.project import Project
from app.retrieval.scoring import compute_scores_json


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
def project(auth_client):
    resp = auth_client.post("/api/v1/projects", json={"name": "findings-api-test-project"})
    assert resp.status_code == 201
    body = resp.json()
    yield body
    db = SessionLocal()
    try:
        row = db.get(Project, body["id"])
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


@pytest.fixture
def run_with_findings(project):
    db = SessionLocal()
    try:
        run = AnalysisRun(project_id=project["id"], status=AnalysisRunStatus.RUNNING)
        db.add(run)
        db.commit()
        db.refresh(run)

        rows = [
            Finding(
                project_id=project["id"],
                analysis_run_id=run.id,
                finding_id="SEO-CANONICAL-001:aaaa",
                observation="canonical missing on https://example.com/",
                problem="canonical missing on https://example.com/",
                evidence=[
                    {
                        "source": "https://example.com/",
                        "excerpt": "canonical is null",
                        "confidence": "direct",
                    }
                ],
                source="Google Search Central",
                source_url="https://developers.google.com/search",
                source_authority=AuthorityLevel.OFFICIAL_VENDOR_DOCS,
                rule="SEO-CANONICAL-001",
                rule_version=1,
                category=RuleCategory.TECHNICAL_SEO,
                severity=RuleSeverity.HIGH,
                confidence=RuleConfidence.HIGH,
                affected_resource="https://example.com/",
                affected_url="https://example.com/",
                affected_code_entity=None,
                expected_mechanism="mechanism",
                recommended_action="add canonical",
                recommendation="add canonical",
                actionability="recommend_only",
                risk="No change has been applied.",
                will_validate="re-check",
                change_worked="not_yet_applied",
                rollback="not applicable",
                status=FindingStatus.OPEN,
            ),
            Finding(
                project_id=project["id"],
                analysis_run_id=run.id,
                finding_id="GEO-ENTITY-CLARITY-001:bbbb",
                observation="entity unclear on https://example.com/about",
                problem="entity unclear on https://example.com/about",
                evidence=[
                    {
                        "source": "https://example.com/about",
                        "excerpt": "no Organization schema",
                        "confidence": "direct",
                    }
                ],
                source="Schema.org",
                source_url="https://schema.org/Organization",
                source_authority=AuthorityLevel.OFFICIAL_STANDARD,
                rule="GEO-ENTITY-CLARITY-001",
                rule_version=1,
                category=RuleCategory.GEO,
                severity=RuleSeverity.LOW,
                confidence=RuleConfidence.MEDIUM,
                affected_resource="https://example.com/about",
                affected_url="https://example.com/about",
                affected_code_entity=None,
                expected_mechanism="mechanism",
                recommended_action="add Organization schema",
                recommendation="add Organization schema",
                actionability="recommend_only",
                risk="No change has been applied.",
                will_validate="re-check",
                change_worked="not_yet_applied",
                rollback="not applicable",
                status=FindingStatus.OPEN,
            ),
        ]
        db.add_all(rows)
        db.commit()
        for row in rows:
            db.refresh(row)

        run.scores_json = compute_scores_json(rows)
        db.commit()
        yield project, run.id
    finally:
        db.close()


def test_latest_analysis_run_carries_scores(auth_client, run_with_findings) -> None:
    project, run_id = run_with_findings
    resp = auth_client.get(f"/api/v1/projects/{project['id']}/analysis-runs/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run_id
    assert set(body["scores_json"]) == {
        "technical_seo_health",
        "content_aeo_readiness",
        "ai_search_geo_readiness",
    }


def test_findings_are_ranked_by_priority(auth_client, run_with_findings) -> None:
    project, _run_id = run_with_findings
    resp = auth_client.get(f"/api/v1/projects/{project['id']}/findings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert [row["priority"] for row in body] == sorted(
        (row["priority"] for row in body), reverse=True
    )
    assert body[0]["rule"] == "SEO-CANONICAL-001"
    assert "priority_factors" in body[0]


def test_findings_filter_by_category(auth_client, run_with_findings) -> None:
    project, _run_id = run_with_findings
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/findings", params={"category": "geo"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["rule"] == "GEO-ENTITY-CLARITY-001"


def test_findings_filter_by_rule(auth_client, run_with_findings) -> None:
    project, _run_id = run_with_findings
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/findings",
        params={"rule": "SEO-CANONICAL-001"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body
    assert all(row["rule"] == "SEO-CANONICAL-001" for row in body)


def test_findings_filter_by_url(auth_client, run_with_findings) -> None:
    project, _run_id = run_with_findings
    resp = auth_client.get(
        f"/api/v1/projects/{project['id']}/findings", params={"url": "about"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["affected_resource"] == "https://example.com/about"


def test_drill_down_from_score_signal_to_finding_evidence(auth_client, run_with_findings) -> None:
    project, run_id = run_with_findings
    run_resp = auth_client.get(f"/api/v1/projects/{project['id']}/analysis-runs/latest")
    signal = run_resp.json()["scores_json"]["technical_seo_health"]["signals"][0]
    assert signal["rule"] == "SEO-CANONICAL-001"
    evidence_ref = signal["evidence"][0]

    findings_resp = auth_client.get(f"/api/v1/projects/{project['id']}/findings")
    match = next(
        row for row in findings_resp.json() if row["finding_id"] == evidence_ref["finding_id"]
    )
    assert match["evidence"][0]["excerpt"] == evidence_ref["excerpt"]

    detail_resp = auth_client.get(f"/api/v1/projects/{project['id']}/findings/{match['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["finding_id"] == evidence_ref["finding_id"]


def test_get_finding_rejects_other_projects(auth_client, run_with_findings) -> None:
    other_resp = auth_client.post("/api/v1/projects", json={"name": "other-findings-project"})
    assert other_resp.status_code == 201
    other_id = other_resp.json()["id"]

    project, _run_id = run_with_findings
    findings_resp = auth_client.get(f"/api/v1/projects/{project['id']}/findings")
    finding_id = findings_resp.json()[0]["id"]

    resp = auth_client.get(f"/api/v1/projects/{other_id}/findings/{finding_id}")
    assert resp.status_code == 404

    db = SessionLocal()
    try:
        row = db.get(Project, other_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
