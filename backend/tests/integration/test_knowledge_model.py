"""`optimization_rules` / `optimization_sources` record shape (step 4.A.1 verify)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.knowledge.authority import AuthorityLevel
from app.models.knowledge import (
    OptimizationRule,
    OptimizationSource,
    RuleCategory,
    RuleConfidence,
    RuleSeverity,
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
def source(db) -> OptimizationSource:
    row = OptimizationSource(
        name="Google Search Central",
        source_url="https://developers.google.com/search/docs/crawling-indexing/canonicalization",
        authority=AuthorityLevel.OFFICIAL_VENDOR_DOCS,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    yield row
    db.rollback()
    existing = db.get(OptimizationSource, row.id)
    if existing is not None:
        db.delete(existing)
        db.commit()


def _rule_kwargs(source: OptimizationSource) -> dict:
    # Not "SEO-CANONICAL-001": since step 4.B.2's ingest job populates the
    # real knowledge base (including that exact rule_id, per the [SPEC]
    # example) into this same database, a schema/constraint test needs its
    # own non-colliding scratch id.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return dict(
        rule_id="TEST-SCHEMA-001",
        category=RuleCategory.TECHNICAL_SEO,
        source_id=source.id,
        source=source.name,
        source_url=source.source_url,
        authority=source.authority,
        published_at=None,
        retrieved_at=now,
        version=1,
        content="A page eligible for indexing should declare a self-referencing or preferred canonical URL.",
        conditions={"page_indexable": True, "canonical_missing": True},
        severity=RuleSeverity.MEDIUM,
        recommendation="Add a rel=canonical link pointing at the preferred URL.",
        confidence=RuleConfidence.HIGH,
    )


def test_a_complete_rule_row_can_be_inserted(db, source) -> None:
    rule = OptimizationRule(**_rule_kwargs(source))
    db.add(rule)
    db.commit()
    db.refresh(rule)
    try:
        assert rule.id is not None
        stored = db.scalar(select(OptimizationRule).where(OptimizationRule.id == rule.id))
        assert stored is not None
        assert stored.authority == AuthorityLevel.OFFICIAL_VENDOR_DOCS
        assert stored.source == "Google Search Central"
    finally:
        db.delete(rule)
        db.commit()


def test_a_rule_row_cannot_be_inserted_without_a_source(db, source) -> None:
    kwargs = _rule_kwargs(source)
    kwargs["source"] = None
    rule = OptimizationRule(**kwargs)
    db.add(rule)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_rule_row_cannot_be_inserted_without_an_authority(db, source) -> None:
    kwargs = _rule_kwargs(source)
    kwargs["authority"] = None
    rule = OptimizationRule(**kwargs)
    db.add(rule)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_rule_id_and_version_pair_is_unique(db, source) -> None:
    first = OptimizationRule(**_rule_kwargs(source))
    db.add(first)
    db.commit()
    db.refresh(first)
    try:
        second = OptimizationRule(**_rule_kwargs(source))
        db.add(second)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        existing = db.get(OptimizationRule, first.id)
        if existing is not None:
            db.delete(existing)
            db.commit()


def test_re_ranking_a_source_does_not_rewrite_an_already_ingested_rule(db, source) -> None:
    """A rule's authority is a snapshot taken at ingestion — re-ranking the
    source afterwards must not retroactively change it [SPEC]."""
    rule = OptimizationRule(**_rule_kwargs(source))
    db.add(rule)
    db.commit()
    db.refresh(rule)
    try:
        source.authority = AuthorityLevel.COMMUNITY_UNVERIFIED
        db.add(source)
        db.commit()

        db.refresh(rule)
        assert rule.authority == AuthorityLevel.OFFICIAL_VENDOR_DOCS
    finally:
        db.delete(rule)
        db.commit()
