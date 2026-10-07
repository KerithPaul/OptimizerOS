"""Rule hits asserted from golden project E EXPECTED.md (step 4.C.2)."""

from tests.unit.rules.conftest import GOLDEN_E_BASE, by_url, evaluate_golden, hit_urls, hits_for


def test_duplicate_titles_index_and_about(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    urls = set(hit_urls("SEO-TITLE-DUPLICATE-001", result))
    assert f"{GOLDEN_E_BASE}/index.html" in urls
    assert f"{GOLDEN_E_BASE}/about.html" in urls
    assert f"{GOLDEN_E_BASE}/products.html" not in urls
    pages = by_url(golden_e_pages)
    assert pages[f"{GOLDEN_E_BASE}/index.html"].title == "Golden E"
    assert pages[f"{GOLDEN_E_BASE}/about.html"].title == "Golden E"


def test_missing_canonical_about_and_products(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    urls = set(hit_urls("SEO-CANONICAL-001", result))
    assert f"{GOLDEN_E_BASE}/about.html" in urls
    assert f"{GOLDEN_E_BASE}/products.html" in urls
    assert f"{GOLDEN_E_BASE}/index.html" not in urls
    assert f"{GOLDEN_E_BASE}/contact.html" not in urls
    assert f"{GOLDEN_E_BASE}/orphan.html" not in urls


def test_missing_alt_on_products_widget(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    hits = hits_for("SEO-IMAGE-ALT-001", result)
    assert hits
    assert any(
        hit.affected_resource.endswith("img:https://golden-e.example/widget.png") for hit in hits
    )
    assert all("ok.png" not in hit.affected_resource for hit in hits)


def test_broken_internal_link_to_missing_html(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    hits = hits_for("SEO-BROKEN-INTERNAL-LINK-001", result)
    assert any(
        hit.affected_resource
        == f"{GOLDEN_E_BASE}/index.html -> {GOLDEN_E_BASE}/missing.html"
        for hit in hits
    )


def test_heading_skip_on_about(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    urls = set(hit_urls("SEO-HEADING-SKIP-001", result))
    assert f"{GOLDEN_E_BASE}/about.html" in urls
    about = by_url(golden_e_pages)[f"{GOLDEN_E_BASE}/about.html"]
    assert [heading.level for heading in about.headings][:2] == [1, 3]


def test_malformed_json_ld_on_contact(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    hits = hits_for("SEO-STRUCTUREDDATA-INVALID-001", result)
    assert any(hit.affected_resource.startswith(f"{GOLDEN_E_BASE}/contact.html") for hit in hits)
    contact = by_url(golden_e_pages)[f"{GOLDEN_E_BASE}/contact.html"]
    assert any(block.parse_error for block in contact.structured_data)


def test_orphan_page_is_orphan_html(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    urls = set(hit_urls("SEO-ORPHAN-PAGE-001", result))
    assert f"{GOLDEN_E_BASE}/orphan.html" in urls
    assert f"{GOLDEN_E_BASE}/index.html" not in urls
    assert f"{GOLDEN_E_BASE}/about.html" not in urls
    assert f"{GOLDEN_E_BASE}/products.html" not in urls
    assert f"{GOLDEN_E_BASE}/contact.html" not in urls


def test_about_question_heading_does_not_fire_question_gap(
    golden_e_pages, golden_e_crawl_stats
) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    about_hits = [
        hit
        for hit in hits_for("AEO-QUESTION-HEADING-001", result)
        if hit.affected_resource == f"{GOLDEN_E_BASE}/about.html"
    ]
    assert about_hits == []
    about = by_url(golden_e_pages)[f"{GOLDEN_E_BASE}/about.html"]
    assert any(heading.text == "What is Golden E?" for heading in about.headings)


def test_about_answer_lead_is_in_the_expected_band(golden_e_pages, golden_e_crawl_stats) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    about_hits = [
        hit
        for hit in hits_for("AEO-ANSWER-LEAD-001", result)
        if f"{GOLDEN_E_BASE}/about.html" in hit.affected_resource
    ]
    assert about_hits == []
    about = by_url(golden_e_pages)[f"{GOLDEN_E_BASE}/about.html"]
    assert about.questions
    words = about.questions[0].answer.split()
    assert 40 <= len(words) <= 80


def test_golden_e_hits_carry_rule_id_version_and_source(
    golden_e_pages, golden_e_crawl_stats
) -> None:
    result = evaluate_golden(golden_e_pages, golden_e_crawl_stats)
    for rule_id in (
        "SEO-CANONICAL-001",
        "SEO-TITLE-DUPLICATE-001",
        "SEO-IMAGE-ALT-001",
        "SEO-HEADING-SKIP-001",
        "SEO-STRUCTUREDDATA-INVALID-001",
        "SEO-ORPHAN-PAGE-001",
        "SEO-BROKEN-INTERNAL-LINK-001",
    ):
        hits = hits_for(rule_id, result)
        assert hits, rule_id
        for hit in hits:
            assert hit.rule_id == rule_id
            assert hit.rule_version == 1
            assert hit.source_url.startswith("https://")
