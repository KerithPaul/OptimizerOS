"""Deterministic evaluator contract (step 4.C.1 verify)."""

from app.knowledge.evaluator import (
    EvaluableRule,
    RuleHit,
    catalog_rules,
    evaluate,
    evaluate_page,
    hits_bytes,
    is_mechanically_executable,
    merge_evaluable_rules,
)
from app.knowledge.rulefile import RuleConditions, load_all_rule_files
from tests.unit.rules.conftest import hits_for, ok_page


def test_same_pages_evaluated_twice_are_byte_identical(golden_e_pages, golden_e_crawl_stats) -> None:
    first = evaluate(golden_e_pages, crawl_stats=golden_e_crawl_stats)
    second = evaluate(golden_e_pages, crawl_stats=golden_e_crawl_stats)
    assert first.hits_bytes() == second.hits_bytes()
    assert hits_bytes(first.hits) == hits_bytes(second.hits)
    assert [hit.model_dump(mode="json") for hit in first.hits] == [
        hit.model_dump(mode="json") for hit in second.hits
    ]


def test_evaluate_page_twice_is_byte_identical(golden_e_pages) -> None:
    about = next(page for page in golden_e_pages if page.url.endswith("/about.html"))
    first = evaluate_page(about, sibling_pages=golden_e_pages)
    second = evaluate_page(about, sibling_pages=golden_e_pages)
    assert first.hits_bytes() == second.hits_bytes()


def test_rule_hit_shape_matches_the_spec(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate(golden_e_pages, crawl_stats=golden_e_crawl_stats)
    assert result.hits
    required = {
        "rule_id",
        "rule_version",
        "severity",
        "affected_resource",
        "observed_value",
        "expected_condition",
        "source_url",
    }
    for hit in result.hits:
        assert set(hit.model_dump().keys()) == required
        assert hit.rule_id
        assert hit.rule_version == 1
        assert hit.source_url.startswith("https://")
        assert hit.severity in {"low", "medium", "high", "critical"}


def test_unknown_page_field_is_recorded_and_not_guessed() -> None:
    rule = EvaluableRule(
        rule_id="SEO-TITLE-MISSING-001",
        version=1,
        severity="high",
        source_url="https://developers.google.com/search/docs/appearance/title-link",
        conditions=RuleConditions(
            applies_to="page",
            evaluation="mechanical",
            check="title_missing",
            field="not_a_real_field",
            operator="is_null",
        ),
    )
    page = ok_page()
    result = evaluate([page], rules=[rule])
    assert result.hits == ()
    assert any(
        skip.reason == "unknown_field" and skip.rule_id == "SEO-TITLE-MISSING-001"
        for skip in result.skips
    )


def test_unknown_check_is_recorded_and_not_guessed() -> None:
    rule = EvaluableRule(
        rule_id="SEO-TITLE-MISSING-001",
        version=1,
        severity="high",
        source_url="https://developers.google.com/search/docs/appearance/title-link",
        conditions=RuleConditions(
            applies_to="page",
            evaluation="mechanical",
            check="definitely_not_a_check",
            note="no field/operator; this check is unknown to the evaluator",
        ),
    )
    result = evaluate([ok_page()], rules=[rule])
    assert result.hits == ()
    assert any(skip.reason == "unknown_check" for skip in result.skips)


def test_llm_interpreted_rules_are_not_evaluated() -> None:
    loaded = {item.rule.rule_id: item.rule for item in load_all_rule_files()}
    intent = loaded["SEO-CONTENT-INTENT-001"]
    assert intent.conditions.evaluation == "llm_interpreted"
    result = evaluate([ok_page()], rules=[EvaluableRule.from_rule_file(intent)])
    assert hits_for("SEO-CONTENT-INTENT-001", result) == []
    assert any(
        skip.reason == "llm_interpreted" and skip.rule_id == "SEO-CONTENT-INTENT-001"
        for skip in result.skips
    )


def test_missing_crawl_stats_does_not_invent_a_robots_hit() -> None:
    result = evaluate([ok_page()])
    assert hits_for("SEO-ROBOTS-UNAVAILABLE-001", result) == []
    assert any(
        skip.rule_id == "SEO-ROBOTS-UNAVAILABLE-001" and skip.reason == "missing_context"
        for skip in result.skips
    )


def test_repository_facts_do_not_change_hits(golden_e_pages, golden_e_crawl_stats) -> None:
    without = evaluate(golden_e_pages, crawl_stats=golden_e_crawl_stats)
    with_facts = evaluate(
        golden_e_pages,
        crawl_stats=golden_e_crawl_stats,
        repository_facts={"framework": "nextjs", "language": "typescript"},
    )
    assert without.hits_bytes() == with_facts.hits_bytes()


def test_every_mechanical_catalog_rule_is_executable() -> None:
    for item in load_all_rule_files():
        if item.rule.conditions.evaluation != "mechanical":
            continue
        assert is_mechanically_executable(item.rule.conditions), item.rule.rule_id


def test_catalog_rules_cover_the_spec_example_id() -> None:
    ids = {rule.rule_id for rule in catalog_rules()}
    assert "SEO-CANONICAL-001" in ids
    assert "SEO-METADESC-LENGTH-001" in ids


def test_merge_evaluable_rules_fills_catalog_gaps_and_prefers_db() -> None:
    catalog = catalog_rules()
    catalog_ids = {rule.rule_id for rule in catalog}
    assert "SEO-METADESC-LENGTH-001" in catalog_ids
    db_only = EvaluableRule(
        rule_id="SEO-TITLE-LENGTH-001",
        version=9,
        severity="high",
        source_url="https://developers.google.com/search/docs/appearance/title-link",
        conditions=RuleConditions(
            applies_to="page",
            evaluation="mechanical",
            check="title_length_likely_truncated",
            field="title",
            operator="length_gt",
            value=60,
        ),
    )
    merged = merge_evaluable_rules([db_only], catalog=catalog)
    by_id = {rule.rule_id: rule for rule in merged}
    assert by_id["SEO-METADESC-LENGTH-001"].version == 1
    assert by_id["SEO-TITLE-LENGTH-001"].version == 9
    assert by_id["SEO-TITLE-LENGTH-001"].severity == "high"


def test_hits_are_sorted_stably() -> None:
    pages = [
        ok_page("https://example.com/b", title=None, canonical=None),
        ok_page("https://example.com/a", title=None, canonical=None),
    ]
    result = evaluate(pages)
    resources = [
        hit.affected_resource
        for hit in result.hits
        if hit.rule_id == "SEO-TITLE-MISSING-001"
    ]
    assert resources == ["https://example.com/a", "https://example.com/b"]


def test_rule_hit_is_not_a_finding_model() -> None:
    fields = set(RuleHit.model_fields)
    assert "hypothesis" not in fields
    assert "confidence" not in fields
    assert "expected_mechanism" not in fields
    assert "risk" not in fields
