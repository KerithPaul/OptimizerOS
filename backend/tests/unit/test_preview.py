"""Post-change preview URL rewrite, sibling overlay, reviewer diff text."""

from app.changes.preview import (
    compose_diff_summary,
    extra_sibling_urls,
    is_site_scoped_check,
    overlay_pages,
    rewrite_live_url_to_preview,
)
from app.connectors.model import Link
from app.knowledge.evaluator import EvaluableRule, evaluate_page
from app.knowledge.rulefile import RuleConditions
from tests.unit.rules.conftest import hits_for, ok_page


def test_rewrite_maps_path_onto_loopback_preview() -> None:
    assert (
        rewrite_live_url_to_preview("https://drmoksha.com/", "http://127.0.0.1:49152")
        == "http://127.0.0.1:49152/"
    )
    assert (
        rewrite_live_url_to_preview("https://drmoksha.com/careers", "http://127.0.0.1:49152")
        == "http://127.0.0.1:49152/careers"
    )


def test_extra_sibling_urls_excludes_targeted_and_respects_limit() -> None:
    crawled = [
        "https://example.com/",
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    extras = extra_sibling_urls(crawled, ["https://example.com/"], limit=2)
    assert extras == ["https://example.com/a", "https://example.com/b"]


def test_orphan_is_site_scoped() -> None:
    assert is_site_scoped_check("orphan_page") is True
    assert is_site_scoped_check("title_missing") is False


def test_overlay_replaces_stored_page_with_fresh_render() -> None:
    stored = [
        ok_page("https://example.com/", links=[]),
        ok_page("https://example.com/about", links=[]),
    ]
    fresh = {
        "https://example.com/about": ok_page(
            "https://example.com/about",
            links=[Link(href="https://example.com/", text="Home", internal=True)],
        )
    }
    overlay = overlay_pages(stored, fresh)
    about = next(page for page in overlay if page.url.endswith("/about"))
    assert any(link.href == "https://example.com/" for link in about.links)


def test_compose_diff_summary_includes_change_summary_when_notes_empty() -> None:
    text = compose_diff_summary(
        notes="",
        files=[
            {
                "file_path": "src/app/layout.tsx",
                "change_summary": "Add a Home link in the shared nav",
                "unified_diff": "@@ -1,0 +1,3 @@\n+<Link href=\"/\">Home</Link>\n",
            }
        ],
    )
    assert "src/app/layout.tsx" in text
    assert "Add a Home link in the shared nav" in text
    assert "href=\"/\"" in text


def test_layout_home_link_on_sibling_clears_orphan_for_inner_page() -> None:
    rule = EvaluableRule(
        rule_id="SEO-ORPHAN-PAGE-001",
        version=1,
        severity="medium",
        source_url="https://developers.google.com/search/docs/crawling-indexing/links-crawlable",
        conditions=RuleConditions(
            applies_to="website",
            evaluation="mechanical",
            check="orphan_page",
            note="inbound internal links from a different URL",
        ),
    )
    about = ok_page("https://example.com/about", links=[])
    stale_home = ok_page("https://example.com/other", links=[])
    patched_other = ok_page(
        "https://example.com/other",
        links=[Link(href="https://example.com/about", text="About", internal=True)],
    )
    before = evaluate_page(about, sibling_pages=[stale_home], rules=[rule])
    assert any(hit.affected_resource == about.url for hit in hits_for("SEO-ORPHAN-PAGE-001", before))
    after = evaluate_page(about, sibling_pages=[patched_other], rules=[rule])
    assert not any(hit.affected_resource == about.url for hit in hits_for("SEO-ORPHAN-PAGE-001", after))
