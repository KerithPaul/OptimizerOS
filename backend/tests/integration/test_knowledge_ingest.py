"""Ingest job idempotency + versioning across MySQL, Qdrant, Neo4j (step 4.B.2 verify).

`optimization_knowledge` has no project/website scoping, unlike the other
two Qdrant collections, so `ingest_knowledge_base` must always be pointed at
the *complete* rule set (step 4.B.2's Qdrant stale-point cleanup compares
against exactly what it is given). These tests point it at a copy of the
real `knowledge/` tree plus one extra `TEST-KB-INGEST-001` rule, so a
real-service run never treats any of the production rules as deleted.
"""

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.intelligence.repository.graph import get_driver
from app.knowledge.ingest import ingest_knowledge_base, rule_stable_id
from app.knowledge.rulefile import KNOWLEDGE_ROOT
from app.models.knowledge import OptimizationRule, OptimizationSource
from app.services.vectors import delete_knowledge_points, scroll_optimization_knowledge

_TEST_RULE_ID = "TEST-KB-INGEST-001"

_TEST_RULE_V1 = """\
rule_id: TEST-KB-INGEST-001
category: technical_seo
severity: low
confidence: medium
source:
  name: "Google Search Central — Influencing title links in Google Search"
  source_url: "https://developers.google.com/search/docs/appearance/title-link"
  authority: official_vendor_docs
retrieved_at: "2026-09-09"
content: "Version one of a throwaway test rule used only by test_knowledge_ingest.py."
conditions:
  applies_to: page
  evaluation: mechanical
  check: test_check
  field: title
  operator: is_null
recommendation: "This is a test fixture, not real guidance."
"""

_TEST_RULE_V2 = _TEST_RULE_V1.replace(
    "Version one of a throwaway test rule",
    "Version TWO of a throwaway test rule",
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    """A full copy of the real `knowledge/` tree plus one test-only rule."""
    root = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE_ROOT, root)
    (root / "seo" / f"{_TEST_RULE_ID}.yaml").write_text(_TEST_RULE_V1, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _cleanup(db):
    yield
    rows = db.scalars(
        select(OptimizationRule).where(OptimizationRule.rule_id == _TEST_RULE_ID)
    ).all()
    for row in rows:
        db.delete(row)
    db.flush()
    source = db.scalar(
        select(OptimizationSource).where(
            OptimizationSource.name == "Google Search Central — Influencing title links in Google Search",
            OptimizationSource.source_url
            == "https://developers.google.com/search/docs/appearance/title-link",
        )
    )
    # Only remove the source if nothing else references it (SEO-TITLE-MISSING-001
    # and SEO-TITLE-LENGTH-001 both cite this exact source in the real catalog).
    if source is not None:
        still_used = db.scalar(
            select(OptimizationRule).where(OptimizationRule.source_id == source.id)
        )
        if still_used is None:
            db.delete(source)
    db.commit()

    delete_knowledge_points([_TEST_RULE_ID])

    driver = get_driver()
    with driver.session() as neo_session:
        neo_session.run(
            "MATCH (n:OptimizationRule {rule_id: $rule_id}) DETACH DELETE n",
            rule_id=_TEST_RULE_ID,
        )


def test_ingesting_twice_creates_exactly_one_version(db, knowledge_root) -> None:
    first = ingest_knowledge_base(db, root=knowledge_root)
    assert first.rules_created >= 1

    second = ingest_knowledge_base(db, root=knowledge_root)
    assert second.rules_created == 0
    assert second.rules_versioned == 0

    rows = db.scalars(
        select(OptimizationRule).where(OptimizationRule.rule_id == _TEST_RULE_ID)
    ).all()
    assert len(rows) == 1
    assert rows[0].version == 1


def test_editing_and_reingesting_yields_two_versions(db, knowledge_root) -> None:
    ingest_knowledge_base(db, root=knowledge_root)

    (knowledge_root / "seo" / f"{_TEST_RULE_ID}.yaml").write_text(_TEST_RULE_V2, encoding="utf-8")
    result = ingest_knowledge_base(db, root=knowledge_root)
    assert result.rules_versioned == 1

    rows = db.scalars(
        select(OptimizationRule)
        .where(OptimizationRule.rule_id == _TEST_RULE_ID)
        .order_by(OptimizationRule.version)
    ).all()
    assert [row.version for row in rows] == [1, 2]
    assert "Version one" in rows[0].content
    assert "Version TWO" in rows[1].content


def test_ingest_writes_qdrant_and_neo4j_for_the_current_version(db, knowledge_root) -> None:
    ingest_knowledge_base(db, root=knowledge_root)

    points = scroll_optimization_knowledge()
    test_points = [p for p in points if p.get("rule_id") == _TEST_RULE_ID]
    assert len(test_points) == 1
    assert test_points[0]["rule_version"] == 1

    driver = get_driver()
    with driver.session() as neo_session:
        record = neo_session.run(
            "MATCH (n:OptimizationRule {rule_id: $rule_id, is_current: true}) "
            "RETURN n.stable_id AS stable_id",
            rule_id=_TEST_RULE_ID,
        ).single()
    assert record is not None
    assert record["stable_id"] == rule_stable_id(_TEST_RULE_ID, 1)


def test_a_new_version_supersedes_the_old_one_in_neo4j(db, knowledge_root) -> None:
    ingest_knowledge_base(db, root=knowledge_root)
    (knowledge_root / "seo" / f"{_TEST_RULE_ID}.yaml").write_text(_TEST_RULE_V2, encoding="utf-8")
    ingest_knowledge_base(db, root=knowledge_root)

    driver = get_driver()
    with driver.session() as neo_session:
        records = list(
            neo_session.run(
                "MATCH (n:OptimizationRule {rule_id: $rule_id}) "
                "RETURN n.version AS version, n.is_current AS is_current "
                "ORDER BY n.version",
                rule_id=_TEST_RULE_ID,
            )
        )
    assert [(r["version"], r["is_current"]) for r in records] == [(1, False), (2, True)]

    points = scroll_optimization_knowledge()
    test_points = [p for p in points if p.get("rule_id") == _TEST_RULE_ID]
    assert len(test_points) == 1
    assert test_points[0]["rule_version"] == 2
