"""Keyword suggestions API reads stored GSC query rows."""

import uuid
from datetime import date, datetime, timezone

import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings as live_get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.finding import AnalysisRun, AnalysisRunStatus
from app.models.project import Project
from app.models.search import SearchConsoleDimension, SearchConsoleRow


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    settings = live_get_settings()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": settings.seed_user_email, "password": settings.seed_user_password},
    )
    assert resp.status_code == 200
    return client


@pytest.fixture
def project(auth_client):
    resp = auth_client.post(
        "/api/v1/projects", json={"name": f"kw-api-{uuid.uuid4()}"}
    )
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


def test_get_without_gsc_rows_is_empty_and_unavailable(auth_client, project) -> None:
    resp = auth_client.get(f"/api/v1/projects/{project['id']}/keyword-suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "gsc"
    assert body["suggestions"] == []
    assert body["suggestion_count"] == 0
    assert body["query_row_count"] == 0
    assert body["connection"]["status"] == "unavailable"
    assert body["connection"]["display"] == "Search Console: unavailable"
    assert "credentials" not in body["connection"]
    assert body["note"].startswith("These rows are queries Google Search Console")
    assert body["filters"] == {
        "dimension": "query",
        "match": "any",
        "rank": "impressions_desc",
        "position_gte": 8.0,
        "ctr_lt": 0.02,
    }


def test_get_returns_query_opportunities_from_latest_run(auth_client, project) -> None:
    db = SessionLocal()
    try:
        old_run = AnalysisRun(project_id=project["id"], status=AnalysisRunStatus.SUCCEEDED)
        new_run = AnalysisRun(project_id=project["id"], status=AnalysisRunStatus.SUCCEEDED)
        db.add_all([old_run, new_run])
        db.flush()
        common = dict(
            project_id=project["id"],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 28),
            fetched_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            clicks=2,
        )
        db.add(
            SearchConsoleRow(
                **common,
                analysis_run_id=old_run.id,
                dimension=SearchConsoleDimension.QUERY,
                query="old run query",
                impressions=9000,
                ctr=0.001,
                position=20.0,
            )
        )
        db.add(
            SearchConsoleRow(
                **common,
                analysis_run_id=new_run.id,
                dimension=SearchConsoleDimension.QUERY,
                query="high impressions weak position",
                impressions=1200,
                ctr=0.05,
                position=12.4,
            )
        )
        db.add(
            SearchConsoleRow(
                **common,
                analysis_run_id=new_run.id,
                dimension=SearchConsoleDimension.QUERY,
                query="low ctr strong position",
                impressions=800,
                ctr=0.008,
                position=3.2,
            )
        )
        db.add(
            SearchConsoleRow(
                **common,
                analysis_run_id=new_run.id,
                dimension=SearchConsoleDimension.QUERY,
                query="strong query excluded",
                impressions=5000,
                ctr=0.08,
                position=2.1,
            )
        )
        db.add(
            SearchConsoleRow(
                **common,
                analysis_run_id=new_run.id,
                dimension=SearchConsoleDimension.PAGE_QUERY,
                query="page query pair",
                page="https://example.com/",
                impressions=7000,
                ctr=0.001,
                position=18.0,
            )
        )
        db.commit()
        latest_run_id = new_run.id
    finally:
        db.close()

    resp = auth_client.get(f"/api/v1/projects/{project['id']}/keyword-suggestions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["analysis_run_id"] == latest_run_id
    assert body["query_row_count"] == 3
    assert body["suggestion_count"] == 2
    queries = [row["query"] for row in body["suggestions"]]
    assert queries == ["high impressions weak position", "low ctr strong position"]
    assert "old run query" not in queries
    assert "strong query excluded" not in queries
    assert "page query pair" not in queries
    first = body["suggestions"][0]
    assert first["source"] == "gsc"
    assert first["reason"] == "1,200 impressions, position 12.4, CTR 5.0%"
    assert first["matched_filters"] == ["position_gte_8"]
    assert first["impressions"] == 1200
    assert body["suggestions"][1]["matched_filters"] == ["ctr_lt_0.02"]


def test_unknown_project_is_404(auth_client) -> None:
    resp = auth_client.get("/api/v1/projects/999999/keyword-suggestions")
    assert resp.status_code == 404
