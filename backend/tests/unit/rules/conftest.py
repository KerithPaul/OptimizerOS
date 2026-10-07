"""Golden project E pages and crawl-run stats for step 4.C tests."""

from pathlib import Path

import pytest

from app.connectors.model import (
    Entity,
    Heading,
    Image,
    Link,
    Page,
    Question,
    RobotsDirectives,
    StructuredData,
)
from app.intelligence.website.extract import extract_page
from app.knowledge.evaluator import EvaluableRule, EvaluationResult, evaluate

_REPO = Path(__file__).resolve().parents[4]
_GOLDEN_E = _REPO / "testdata" / "golden-projects" / "e-static-html"
GOLDEN_E_BASE = "https://golden-e.example"
GOLDEN_E_FILES = (
    "index.html",
    "about.html",
    "products.html",
    "contact.html",
    "orphan.html",
)


def golden_e_page(name: str) -> Page:
    html = (_GOLDEN_E / name).read_text(encoding="utf-8")
    return extract_page(html, url=f"{GOLDEN_E_BASE}/{name}").page


@pytest.fixture(scope="module")
def golden_e_pages() -> list[Page]:
    return [golden_e_page(name) for name in GOLDEN_E_FILES]


@pytest.fixture(scope="module")
def golden_e_crawl_stats() -> dict:
    """Crawled-fixture stats: missing.html is the planted broken target."""

    return {
        "failed_urls": [f"{GOLDEN_E_BASE}/missing.html"],
        "robots": {
            "state": "fetched",
            "http_status": 200,
            "ai_bot_disallows": [],
            "sitemap_urls": [],
        },
        "sitemap": {"records": [], "page_url_count": 0},
    }


def by_url(pages: list[Page]) -> dict[str, Page]:
    return {page.url: page for page in pages}


def evaluate_golden(
    pages: list[Page],
    crawl_stats: dict | None = None,
    **kwargs,
) -> EvaluationResult:
    return evaluate(pages, crawl_stats=crawl_stats, **kwargs)


def hits_for(rule_id: str, result: EvaluationResult) -> list:
    return [hit for hit in result.hits if hit.rule_id == rule_id]


def hit_urls(rule_id: str, result: EvaluationResult) -> list[str]:
    return [hit.affected_resource for hit in hits_for(rule_id, result)]


def ok_page(url: str = "https://example.com/ok", **overrides) -> Page:
    """A constructed page that does not trip most mechanical rules."""

    data: dict = {
        "url": url,
        "title": f"Unique title for {url}",
        "meta_description": f"Unique description for {url}",
        "canonical": url,
        "headings": [Heading(level=1, text="Topic"), Heading(level=2, text="What is this page?")],
        "content": " ".join(["word"] * 300),
        "word_count": 300,
        "language": "en",
        "viewport": "width=device-width, initial-scale=1",
        "sitemap_member": True,
        "status_code": 200,
        "robots": RobotsDirectives(meta="index,follow"),
        "structured_data": [
            StructuredData(
                format="json-ld",
                type="Organization",
                parsed={"@type": "Organization", "name": "Example"},
                parse_error=None,
            )
        ],
        "entities": [Entity(name="Example", type="Organization")],
        "open_graph": {
            "og:title": "Topic",
            "og:type": "website",
            "og:image": "https://example.com/i.png",
            "og:url": url,
        },
        "links": [
            Link(href=url, text="self", internal=True),
            Link(href="https://other.example/source", text="ref", internal=False),
        ],
        "images": [Image(src="https://example.com/i.png", alt="a widget", width=10, height=10)],
        "questions": [Question(text="What is this page?", answer=" ".join(["answer"] * 50))],
        "raw_html_hash": f"hash-{url}",
    }
    data.update(overrides)
    return Page(**data)


def evaluate_rule(
    pages: list[Page] | Page,
    *,
    crawl_stats: dict | None = None,
    repository_facts: dict | None = None,
    extra_rules: list[EvaluableRule] | None = None,
) -> EvaluationResult:
    rules = extra_rules
    return evaluate(
        pages,
        crawl_stats=crawl_stats,
        repository_facts=repository_facts,
        rules=rules,
    )
