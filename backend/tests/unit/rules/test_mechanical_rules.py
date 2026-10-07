"""One test per deterministic catalog rule (step 4.C.2).

Golden project E covers the planted EXPECTED.md defects. Crawled fixtures
(constructed Common Website Model pages + crawl_run stats) cover the rest.
"""

from app.connectors.model import Heading, Image, Link, Question, RobotsDirectives, StructuredData
from app.knowledge.evaluator import evaluate
from tests.unit.rules.conftest import GOLDEN_E_BASE, hits_for, ok_page


def _urls(rule_id: str, pages, crawl_stats=None) -> set[str]:
    result = evaluate(pages, crawl_stats=crawl_stats)
    return {hit.affected_resource for hit in hits_for(rule_id, result)}


# --- SEO ------------------------------------------------------------------------


def test_SEO_CANONICAL_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-CANONICAL-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/about.html" in urls
    assert f"{GOLDEN_E_BASE}/products.html" in urls


def test_SEO_CANONICAL_CROSSDOMAIN_001() -> None:
    page = ok_page(canonical="https://other.example/copy")
    urls = _urls("SEO-CANONICAL-CROSSDOMAIN-001", [page])
    assert page.url in urls
    same_site = ok_page(canonical="https://www.example.com/ok")
    assert same_site.url not in _urls("SEO-CANONICAL-CROSSDOMAIN-001", [same_site])


def test_SEO_TITLE_MISSING_001() -> None:
    page = ok_page(title=None)
    assert page.url in _urls("SEO-TITLE-MISSING-001", [page])
    present = ok_page()
    assert present.url not in _urls("SEO-TITLE-MISSING-001", [present])


def test_SEO_TITLE_DUPLICATE_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-TITLE-DUPLICATE-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/index.html" in urls
    assert f"{GOLDEN_E_BASE}/about.html" in urls


def test_SEO_TITLE_LENGTH_001() -> None:
    long_title = "A" * 61
    page = ok_page(title=long_title)
    assert page.url in _urls("SEO-TITLE-LENGTH-001", [page])
    short = ok_page(title="Short unique title")
    assert short.url not in _urls("SEO-TITLE-LENGTH-001", [short])


def test_SEO_METADESC_MISSING_001() -> None:
    page = ok_page(meta_description=None)
    assert page.url in _urls("SEO-METADESC-MISSING-001", [page])


def test_SEO_METADESC_LENGTH_001() -> None:
    long_desc = "A" * 161
    page = ok_page(meta_description=long_desc)
    assert page.url in _urls("SEO-METADESC-LENGTH-001", [page])
    short = ok_page(meta_description="A concise unique description of this page.")
    assert short.url not in _urls("SEO-METADESC-LENGTH-001", [short])


def test_SEO_METADESC_DUPLICATE_001() -> None:
    a = ok_page("https://example.com/a", meta_description="Same description")
    b = ok_page("https://example.com/b", meta_description="Same description")
    urls = _urls("SEO-METADESC-DUPLICATE-001", [a, b])
    assert a.url in urls
    assert b.url in urls
    unique = ok_page("https://example.com/c", meta_description="Different")
    assert unique.url not in _urls("SEO-METADESC-DUPLICATE-001", [a, b, unique])


def test_SEO_H1_MISSING_001() -> None:
    page = ok_page(headings=[Heading(level=2, text="What is this page?")])
    assert page.url in _urls("SEO-H1-MISSING-001", [page])


def test_SEO_H1_MULTIPLE_001() -> None:
    page = ok_page(
        headings=[
            Heading(level=1, text="One"),
            Heading(level=1, text="Two"),
            Heading(level=2, text="What is this page?"),
        ]
    )
    assert page.url in _urls("SEO-H1-MULTIPLE-001", [page])


def test_SEO_HEADING_SKIP_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-HEADING-SKIP-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/about.html" in urls


def test_SEO_HEADING_SKIP_001_consecutive_jump_after_earlier_h3() -> None:
    page = ok_page(
        headings=[
            Heading(level=1, text="Topic"),
            Heading(level=2, text="Section"),
            Heading(level=3, text="Detail"),
            Heading(level=2, text="Next section"),
            Heading(level=4, text="Skipped"),
        ]
    )
    assert page.url in _urls("SEO-HEADING-SKIP-001", [page])
    sequential = ok_page(
        headings=[
            Heading(level=1, text="Topic"),
            Heading(level=2, text="Section"),
            Heading(level=3, text="Detail"),
            Heading(level=2, text="Next"),
        ]
    )
    assert sequential.url not in _urls("SEO-HEADING-SKIP-001", [sequential])


def test_SEO_IMAGE_ALT_001(golden_e_pages, golden_e_crawl_stats) -> None:
    resources = _urls("SEO-IMAGE-ALT-001", golden_e_pages, golden_e_crawl_stats)
    assert any("widget.png" in resource for resource in resources)


def test_SEO_IMAGE_DIMENSIONS_001() -> None:
    page = ok_page(images=[Image(src="https://example.com/bare.png", alt="ok", width=None, height=None)])
    resources = _urls("SEO-IMAGE-DIMENSIONS-001", [page])
    assert any("bare.png" in resource for resource in resources)
    sized = ok_page()
    assert not any(
        sized.url in resource for resource in _urls("SEO-IMAGE-DIMENSIONS-001", [sized])
    )


def test_SEO_IMAGE_rules_skip_data_uri_and_one_by_one_placeholders() -> None:
    page = ok_page(
        images=[
            Image(src="data:image/svg+xml;utf8,<svg></svg>", alt=None, width=None, height=None),
            Image(src="https://example.com/pixel.gif", alt=None, width=1, height=1),
            Image(src="https://example.com/hero.png", alt=None, width=None, height=None),
        ]
    )
    alt_resources = _urls("SEO-IMAGE-ALT-001", [page])
    dim_resources = _urls("SEO-IMAGE-DIMENSIONS-001", [page])
    assert any("hero.png" in resource for resource in alt_resources)
    assert any("hero.png" in resource for resource in dim_resources)
    assert not any("data:" in resource for resource in alt_resources)
    assert not any("pixel.gif" in resource for resource in alt_resources)
    assert not any("data:" in resource for resource in dim_resources)
    assert not any("pixel.gif" in resource for resource in dim_resources)


def test_SEO_BROKEN_INTERNAL_LINK_001(golden_e_pages, golden_e_crawl_stats) -> None:
    resources = _urls("SEO-BROKEN-INTERNAL-LINK-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/index.html -> {GOLDEN_E_BASE}/missing.html" in resources


def test_SEO_BROKEN_INTERNAL_LINK_001_skips_cloudflare_email_protection() -> None:
    href = "https://example.com/cdn-cgi/l/email-protection"
    page = ok_page(
        "https://example.com/article",
        links=[
            Link(href="https://example.com/article", text="self", internal=True),
            Link(href=href, text="email", internal=True),
        ],
    )
    error = ok_page(href, status_code=404, canonical=href, title="Email protection")
    resources = _urls("SEO-BROKEN-INTERNAL-LINK-001", [page, error])
    assert not any("cdn-cgi" in resource for resource in resources)


def test_SEO_BROKEN_INTERNAL_LINK_001_still_fires_for_editorial_404() -> None:
    missing = "https://example.com/retired"
    page = ok_page(
        "https://example.com/article",
        links=[
            Link(href="https://example.com/article", text="self", internal=True),
            Link(href=missing, text="old post", internal=True),
        ],
    )
    error = ok_page(missing, status_code=404, canonical=missing, title="Gone")
    resources = _urls("SEO-BROKEN-INTERNAL-LINK-001", [page, error])
    assert f"https://example.com/article -> {missing}" in resources


def test_SEO_CONTENT_DUPLICATE_001() -> None:
    a = ok_page("https://example.com/a", raw_html_hash="abc123", canonical="https://example.com/a")
    b = ok_page("https://example.com/b", raw_html_hash="abc123", canonical="https://example.com/b")
    urls = _urls("SEO-CONTENT-DUPLICATE-001", [a, b])
    assert a.url in urls
    assert b.url in urls
    canon = ok_page(
        "https://example.com/copy",
        raw_html_hash="abc123",
        canonical="https://example.com/a",
    )
    canon_urls = _urls("SEO-CONTENT-DUPLICATE-001", [a, canon])
    assert canon.url not in canon_urls
    assert a.url not in canon_urls


def test_SEO_CONTENT_THIN_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-CONTENT-THIN-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/index.html" in urls
    thick = ok_page(word_count=400, content=" ".join(["word"] * 400))
    assert thick.url not in _urls("SEO-CONTENT-THIN-001", [thick])


def test_SEO_HREFLANG_NOT_RECIPROCAL_001() -> None:
    from app.connectors.model import Hreflang

    a = ok_page(
        "https://example.com/en",
        hreflang=[Hreflang(lang="fr", href="https://example.com/fr")],
    )
    b = ok_page("https://example.com/fr", hreflang=[])
    resources = _urls("SEO-HREFLANG-NOT-RECIPROCAL-001", [a, b])
    assert any("https://example.com/en hreflang:https://example.com/fr" == item for item in resources)
    reciprocal = ok_page(
        "https://example.com/fr",
        hreflang=[Hreflang(lang="en", href="https://example.com/en")],
    )
    assert not _urls("SEO-HREFLANG-NOT-RECIPROCAL-001", [a, reciprocal])


def test_SEO_LANG_MISSING_001() -> None:
    page = ok_page(language=None)
    assert page.url in _urls("SEO-LANG-MISSING-001", [page])


def test_SEO_NOINDEX_UNEXPECTED_001() -> None:
    page = ok_page(
        sitemap_member=True,
        robots=RobotsDirectives(meta="noindex,follow"),
    )
    assert page.url in _urls("SEO-NOINDEX-UNEXPECTED-001", [page])
    not_in_sitemap = ok_page(
        sitemap_member=False,
        robots=RobotsDirectives(meta="noindex"),
    )
    assert not_in_sitemap.url not in _urls("SEO-NOINDEX-UNEXPECTED-001", [not_in_sitemap])


def test_SEO_OG_INCOMPLETE_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-OG-INCOMPLETE-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/index.html" in urls
    complete = ok_page()
    assert complete.url not in _urls("SEO-OG-INCOMPLETE-001", [complete])


def test_SEO_OG_INCOMPLETE_001_missing_image_fires() -> None:
    page = ok_page(
        open_graph={
            "og:title": "Contact",
            "og:type": "website",
            "og:url": "https://example.com/contact",
        }
    )
    assert page.url in _urls("SEO-OG-INCOMPLETE-001", [page])


def test_SEO_OG_INCOMPLETE_001_empty_image_still_fires() -> None:
    page = ok_page(
        open_graph={
            "og:title": "Contact",
            "og:type": "website",
            "og:url": "https://example.com/contact",
            "og:image": "  ",
        }
    )
    assert page.url in _urls("SEO-OG-INCOMPLETE-001", [page])


def test_SEO_OG_INCOMPLETE_001_image_url_alias_counts() -> None:
    page = ok_page(
        open_graph={
            "og:title": "Contact",
            "og:type": "website",
            "og:url": "https://example.com/contact",
            "og:image:url": "https://example.com/logo.png",
        }
    )
    assert page.url not in _urls("SEO-OG-INCOMPLETE-001", [page])


def test_SEO_OG_INCOMPLETE_001_skips_pages_with_no_og() -> None:
    page = ok_page(open_graph={})
    assert page.url not in _urls("SEO-OG-INCOMPLETE-001", [page])


def test_SEO_ORPHAN_PAGE_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-ORPHAN-PAGE-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/orphan.html" in urls
    assert f"{GOLDEN_E_BASE}/index.html" not in urls


def test_SEO_ORPHAN_PAGE_001_does_not_flag_site_root() -> None:
    home = ok_page("https://example.com/", links=[])
    about = ok_page("https://example.com/about", links=[])
    urls = _urls("SEO-ORPHAN-PAGE-001", [home, about])
    assert "https://example.com/" not in urls
    assert "https://example.com/about" in urls


def test_SEO_ORPHAN_PAGE_001_lone_homepage_is_not_an_orphan() -> None:
    home = ok_page("https://example.com/", links=[])
    assert "https://example.com/" not in _urls("SEO-ORPHAN-PAGE-001", [home])


def test_SEO_REDIRECT_CHAIN_001() -> None:
    page = ok_page(redirect_chain=["https://example.com/hop1", "https://example.com/hop2"])
    assert page.url in _urls("SEO-REDIRECT-CHAIN-001", [page])
    single = ok_page(redirect_chain=["https://example.com/final"])
    assert single.url not in _urls("SEO-REDIRECT-CHAIN-001", [single])


def test_SEO_ROBOTS_UNAVAILABLE_001() -> None:
    stats = {"robots": {"state": "unavailable", "ai_bot_disallows": []}, "sitemap": {"records": []}}
    result = evaluate([ok_page()], crawl_stats=stats)
    assert hits_for("SEO-ROBOTS-UNAVAILABLE-001", result)
    fetched = {"robots": {"state": "fetched", "ai_bot_disallows": []}, "sitemap": {"records": []}}
    assert not hits_for("SEO-ROBOTS-UNAVAILABLE-001", evaluate([ok_page()], crawl_stats=fetched))


def test_SEO_SITEMAP_COVERAGE_GAP_001() -> None:
    page = ok_page(sitemap_member=False)
    assert page.url in _urls("SEO-SITEMAP-COVERAGE-GAP-001", [page])
    member = ok_page(sitemap_member=True)
    assert member.url not in _urls("SEO-SITEMAP-COVERAGE-GAP-001", [member])


def test_SEO_SITEMAP_INVALID_001() -> None:
    stats = {
        "robots": {"state": "fetched", "ai_bot_disallows": []},
        "sitemap": {
            "records": [
                {"url": "https://example.com/sitemap.xml", "parse_state": "invalid", "kind": "unknown"}
            ]
        },
    }
    result = evaluate([ok_page()], crawl_stats=stats)
    hits = hits_for("SEO-SITEMAP-INVALID-001", result)
    assert hits
    assert hits[0].affected_resource == "https://example.com/sitemap.xml"
    valid = {
        "robots": {"state": "fetched", "ai_bot_disallows": []},
        "sitemap": {"records": [{"url": "https://example.com/sitemap.xml", "parse_state": "valid"}]},
    }
    assert not hits_for("SEO-SITEMAP-INVALID-001", evaluate([ok_page()], crawl_stats=valid))


def test_SEO_STRUCTUREDDATA_INVALID_001(golden_e_pages, golden_e_crawl_stats) -> None:
    resources = _urls("SEO-STRUCTUREDDATA-INVALID-001", golden_e_pages, golden_e_crawl_stats)
    assert any(resource.startswith(f"{GOLDEN_E_BASE}/contact.html") for resource in resources)


def test_SEO_STRUCTUREDDATA_MISSING_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("SEO-STRUCTUREDDATA-MISSING-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/index.html" in urls
    assert f"{GOLDEN_E_BASE}/contact.html" not in urls


def test_SEO_URL_COMPLEXITY_001() -> None:
    page = ok_page("https://example.com/a?a=1&b=2&c=3&d=4", canonical="https://example.com/a")
    assert page.url in _urls("SEO-URL-COMPLEXITY-001", [page])
    deep = ok_page("https://example.com/a/b/c/d/e/f/g", canonical="https://example.com/a/b/c/d/e/f/g")
    assert deep.url in _urls("SEO-URL-COMPLEXITY-001", [deep])
    simple = ok_page("https://example.com/ok")
    assert simple.url not in _urls("SEO-URL-COMPLEXITY-001", [simple])


def test_SEO_VIEWPORT_MISSING_001() -> None:
    page = ok_page(viewport=None)
    assert page.url in _urls("SEO-VIEWPORT-MISSING-001", [page])


# --- AEO ------------------------------------------------------------------------


def test_AEO_ANSWER_LEAD_001() -> None:
    missing = ok_page(questions=[Question(text="What is this page?", answer=None)])
    assert any(
        missing.url in resource for resource in _urls("AEO-ANSWER-LEAD-001", [missing])
    )
    verbose = ok_page(questions=[Question(text="What is this page?", answer=" ".join(["word"] * 120))])
    assert any(verbose.url in resource for resource in _urls("AEO-ANSWER-LEAD-001", [verbose]))
    in_band = ok_page()
    assert not any(in_band.url in resource for resource in _urls("AEO-ANSWER-LEAD-001", [in_band]))


def test_AEO_AUTHORSHIP_DATE_001() -> None:
    page = ok_page(
        structured_data=[
            StructuredData(
                format="json-ld",
                type="Article",
                parsed={"@type": "Article", "headline": "Hello"},
                parse_error=None,
            )
        ]
    )
    assert any(page.url in resource for resource in _urls("AEO-AUTHORSHIP-DATE-001", [page]))
    with_author = ok_page(
        structured_data=[
            StructuredData(
                format="json-ld",
                type="Article",
                parsed={"@type": "Article", "author": "Ada"},
                parse_error=None,
            )
        ]
    )
    assert not any(
        with_author.url in resource for resource in _urls("AEO-AUTHORSHIP-DATE-001", [with_author])
    )


def test_AEO_FAQ_SCHEMA_001() -> None:
    page = ok_page(
        questions=[
            Question(text="Q1?", answer=" ".join(["answer"] * 50)),
            Question(text="Q2?", answer=" ".join(["other"] * 50)),
        ],
        structured_data=[
            StructuredData(
                format="json-ld",
                type="Organization",
                parsed={"@type": "Organization", "name": "Example"},
                parse_error=None,
            )
        ],
    )
    assert page.url in _urls("AEO-FAQ-SCHEMA-001", [page])
    marked = ok_page(
        questions=[
            Question(text="Q1?", answer=" ".join(["answer"] * 50)),
            Question(text="Q2?", answer=" ".join(["other"] * 50)),
        ],
        structured_data=[
            StructuredData(
                format="json-ld",
                type="FAQPage",
                parsed={"@type": "FAQPage"},
                parse_error=None,
            )
        ],
    )
    assert marked.url not in _urls("AEO-FAQ-SCHEMA-001", [marked])


def test_AEO_QUESTION_HEADING_001(golden_e_pages, golden_e_crawl_stats) -> None:
    urls = _urls("AEO-QUESTION-HEADING-001", golden_e_pages, golden_e_crawl_stats)
    assert f"{GOLDEN_E_BASE}/index.html" in urls
    assert f"{GOLDEN_E_BASE}/about.html" not in urls


# --- GEO ------------------------------------------------------------------------


def test_GEO_AI_CRAWLER_ACCESS_001() -> None:
    stats = {
        "robots": {"state": "fetched", "ai_bot_disallows": ["gptbot"]},
        "sitemap": {"records": []},
    }
    result = evaluate([ok_page()], crawl_stats=stats)
    assert hits_for("GEO-AI-CRAWLER-ACCESS-001", result)
    empty = {
        "robots": {"state": "fetched", "ai_bot_disallows": []},
        "sitemap": {"records": []},
    }
    assert not hits_for("GEO-AI-CRAWLER-ACCESS-001", evaluate([ok_page()], crawl_stats=empty))


def test_GEO_ENTITY_CLARITY_001() -> None:
    page = ok_page(
        word_count=300,
        entities=[],
        structured_data=[],
        questions=[Question(text="What is this page?", answer=" ".join(["answer"] * 50))],
        headings=[Heading(level=1, text="Topic"), Heading(level=2, text="What is this page?")],
    )
    assert page.url in _urls("GEO-ENTITY-CLARITY-001", [page])
    marked = ok_page()
    assert marked.url not in _urls("GEO-ENTITY-CLARITY-001", [marked])


def test_GEO_SOURCE_ATTRIBUTION_001() -> None:
    page = ok_page(
        word_count=400,
        links=[Link(href="https://example.com/ok", text="self", internal=True)],
    )
    assert page.url in _urls("GEO-SOURCE-ATTRIBUTION-001", [page])
    cited = ok_page(word_count=400)
    assert cited.url not in _urls("GEO-SOURCE-ATTRIBUTION-001", [cited])


def test_GEO_STRUCTURED_DENSITY_001() -> None:
    page = ok_page(
        word_count=800,
        structured_data=[],
        headings=[Heading(level=1, text="Topic"), Heading(level=2, text="What is this page?")],
        questions=[Question(text="What is this page?", answer=" ".join(["answer"] * 50))],
        entities=[],
    )
    assert page.url in _urls("GEO-STRUCTURED-DENSITY-001", [page])
    marked = ok_page(word_count=800)
    assert marked.url not in _urls("GEO-STRUCTURED-DENSITY-001", [marked])
