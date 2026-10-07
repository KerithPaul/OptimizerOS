"""Evidence Engine (step 5.B.2 verify)."""

import pytest
from pydantic import ValidationError

from app.knowledge.evaluator import RuleHit, evaluate
from app.retrieval.evidence import (
    EmptyEvidenceError,
    EvidenceConfidence,
    FindingRecord,
    NINE_QUESTIONS,
    assemble_finding,
    assemble_findings,
    catalog_rule_meta,
    finding_id_for,
    resolve_rule_meta,
    unanswered_questions,
)
from app.retrieval.hybrid import CompressedEvidence
from tests.unit.rules.conftest import golden_e_page


def _hit(**overrides) -> RuleHit:
    payload = dict(
        rule_id="SEO-CANONICAL-001",
        rule_version=1,
        severity="medium",
        affected_resource="https://golden-e.example/about.html",
        observed_value=None,
        expected_condition="canonical is_null",
        source_url="https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    )
    payload.update(overrides)
    return RuleHit(**payload)


def test_empty_evidence_cannot_be_a_finding() -> None:
    meta = resolve_rule_meta(_hit(), catalog_rule_meta())
    assert meta is not None
    record = assemble_finding(_hit(), meta)
    payload = record.model_dump()
    payload["evidence"] = []
    with pytest.raises((EmptyEvidenceError, ValidationError)):
        FindingRecord.model_validate(payload)


def test_finding_answers_the_nine_questions() -> None:
    meta = resolve_rule_meta(_hit(), catalog_rule_meta())
    assert meta is not None
    record = assemble_finding(_hit(), meta)
    assert unanswered_questions(record) == []
    answers = record.nine_questions()
    assert set(answers) == {question for _field, question in NINE_QUESTIONS}
    for question, value in answers.items():
        if question == "WHAT EVIDENCE PROVES IT?":
            assert value
            continue
        assert str(value).strip(), question


def test_measured_rule_hit_is_the_fact_origin_retrieved_context_is_derived() -> None:
    meta = resolve_rule_meta(_hit(), catalog_rule_meta())
    assert meta is not None
    retrieved = [
        CompressedEvidence(
            id="knowledge-1",
            source_type="knowledge",
            channel="semantic",
            score=0.9,
            locator="SEO-CANONICAL-001",
            summary="Google documentation on rel=canonical.",
            metadata={"authority": "official_vendor_docs", "rule_id": "SEO-CANONICAL-001"},
        )
    ]
    record = assemble_finding(_hit(), meta, retrieved=retrieved)
    assert record.evidence[0].confidence is EvidenceConfidence.DIRECT
    assert record.evidence[0].value is None
    assert record.evidence[0].source == "https://golden-e.example/about.html"
    derived = [row for row in record.evidence[1:] if row.confidence is EvidenceConfidence.DERIVED]
    assert len(derived) == 1
    assert derived[0].excerpt == "Google documentation on rel=canonical."
    assert record.rule == "SEO-CANONICAL-001"
    assert record.affected_resource == "https://golden-e.example/about.html"
    assert record.affected_url == "https://golden-e.example/about.html"
    assert record.source_authority.value == "official_vendor_docs"
    assert record.finding_id == finding_id_for(
        "SEO-CANONICAL-001", "https://golden-e.example/about.html"
    )


def test_golden_e_hits_assemble_grounded_findings() -> None:
    pages = [
        golden_e_page("index.html"),
        golden_e_page("about.html"),
        golden_e_page("products.html"),
        golden_e_page("contact.html"),
        golden_e_page("orphan.html"),
    ]
    result = evaluate(
        pages,
        crawl_stats={"failed_urls": ["https://golden-e.example/missing.html"]},
    )
    records = assemble_findings(result.hits, catalog_rule_meta())
    assert records
    by_rule = {record.rule for record in records}
    assert "SEO-CANONICAL-001" in by_rule
    assert "SEO-TITLE-DUPLICATE-001" in by_rule
    for record in records:
        assert record.evidence
        assert record.evidence[0].confidence is EvidenceConfidence.DIRECT
        assert record.rule
        assert record.affected_resource
        assert record.confidence
        assert record.risk
        assert unanswered_questions(record) == []
