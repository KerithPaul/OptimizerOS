"""Golden project E extraction (step 3.C.1 verify)."""

from pathlib import Path

from app.intelligence.website.extract import (
    classify_llms_text,
    extract_page,
)

_REPO = Path(__file__).resolve().parents[3]
_GOLDEN_E = _REPO / "testdata" / "golden-projects" / "e-static-html"
_BASE = "https://golden-e.example"


def _extract(name: str):
    html = (_GOLDEN_E / name).read_text(encoding="utf-8")
    return extract_page(html, url=f"{_BASE}/{name}")


def test_duplicate_titles_match_expected() -> None:
    index = _extract("index.html")
    about = _extract("about.html")
    assert index.page.title == "Golden E"
    assert about.page.title == "Golden E"
    assert index.page.title == about.page.title


def test_missing_canonical_on_about_and_products() -> None:
    about = _extract("about.html")
    products = _extract("products.html")
    index = _extract("index.html")
    contact = _extract("contact.html")
    orphan = _extract("orphan.html")
    assert about.page.canonical is None
    assert products.page.canonical is None
    assert index.page.canonical == f"{_BASE}/index.html"
    assert contact.page.canonical == f"{_BASE}/contact.html"
    assert orphan.page.canonical == f"{_BASE}/orphan.html"


def test_missing_alt_on_products_image() -> None:
    products = _extract("products.html")
    alts = [image.alt for image in products.page.images]
    assert None in alts
    assert any(image.alt == "A labelled widget" for image in products.page.images)


def test_broken_internal_link_is_recorded() -> None:
    index = _extract("index.html")
    hrefs = [link.href for link in index.page.links]
    assert any(href.endswith("/missing.html") for href in hrefs)
    assert not (_GOLDEN_E / "missing.html").exists()


def test_heading_skip_on_about() -> None:
    about = _extract("about.html")
    levels = [heading.level for heading in about.page.headings]
    assert levels[0] == 1
    assert levels[1] == 3


def test_malformed_json_ld_is_recorded_not_dropped() -> None:
    contact = _extract("contact.html")
    assert any(block.parse_error for block in contact.page.structured_data)
    assert any(
        block.parse_error is None and block.type == "Organization"
        for block in contact.page.structured_data
    )


def test_orphan_page_has_no_inbound_internal_links_from_other_pages() -> None:
    names = ["index.html", "about.html", "products.html", "contact.html"]
    inbound = []
    for name in names:
        extracted = _extract(name)
        inbound.extend(
            link.href
            for link in extracted.page.links
            if link.internal and link.href.endswith("/orphan.html")
        )
    assert inbound == []
    orphan = _extract("orphan.html")
    assert orphan.page.title == "Orphan page"


def test_aeo_lite_observations_on_about() -> None:
    about = _extract("about.html")
    assert "What is Golden E?" in about.observations.question_headings
    assert about.observations.concise_answer_leads == 1
    assert about.page.questions
    assert about.page.questions[0].text == "What is Golden E?"
    assert about.page.questions[0].answer is not None
    words = about.page.questions[0].answer.split()
    assert 40 <= len(words) <= 80
    assert "author meta tag" in about.observations.authorship_signals
    assert "time datetime markup" in about.observations.date_markup


def test_index_extracts_lang_viewport_og_twitter_hreflang_robots() -> None:
    index = _extract("index.html")
    page = index.page
    assert page.language == "en"
    assert page.viewport is not None
    assert page.open_graph.get("og:title") == "Golden E"
    assert page.twitter.get("twitter:card") == "summary"
    assert page.hreflang
    assert page.hreflang[0].lang == "en"
    assert page.robots is not None
    assert page.robots.meta == "index,follow"
    assert page.raw_html_hash
    assert page.word_count is not None and page.word_count > 0


def test_golden_e_llms_txt_is_present() -> None:
    text = (_GOLDEN_E / "llms.txt").read_text(encoding="utf-8")
    assert classify_llms_text(text) == "present"


def test_visible_word_count_excludes_nav_and_footer() -> None:
    html = """
    <html><body>
      <nav>Home About Contact repeated chrome words here</nav>
      <main><p>Only the unique article words should count toward thin-content.</p></main>
      <footer role="contentinfo">Copyright navigation more chrome padding words</footer>
    </body></html>
    """
    page = extract_page(html, url="https://example.com/article").page
    assert page.word_count is not None
    assert "Copyright" not in (page.content or "")
    assert "unique article words" in (page.content or "")
    assert page.word_count < 20


def test_next_image_optimizer_src_is_unwrapped() -> None:
    html = """
    <html><body>
      <img
        src="/_next/image?url=https%3A%2F%2Fimg.youtube.com%2Fvi%2Fabc%2Fhqdefault.jpg&w=3840&q=75"
        alt=""
      />
      <img src="hero.png" alt="Hero" width="800" style="height: 400px" />
    </body></html>
    """
    page = extract_page(html, url="https://drmoksha.com/").page
    srcs = [image.src for image in page.images]
    assert "https://img.youtube.com/vi/abc/hqdefault.jpg" in srcs
    hero = next(image for image in page.images if image.src.endswith("hero.png"))
    assert hero.width == 800
    assert hero.height == 400
