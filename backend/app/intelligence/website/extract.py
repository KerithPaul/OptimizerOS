"""Static HTML extraction into the Common Website Model — checkpoint 3.C.1.

Uses selectolax `[P17]`. Does not score. AEO/GEO-lite signals are
recorded as observations for Phase 4 rules.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from app.connectors.model import (
    Heading,
    Hreflang,
    Image,
    Link,
    Page,
    Question,
    RobotsDirectives,
    StructuredData,
)
from app.intelligence.website.crawler import CrawledPage
from app.intelligence.website.urls import (
    normalize_url,
    origin_of,
    same_origin,
)
from app.intelligence.website.webmcp import detect_agent_tools

_QUESTION_RE = re.compile(
    r"\?$|^(?:what|why|how|when|where|who|which|can|does|do|is|are)\b",
    re.IGNORECASE,
)
_FAQ_TYPE_NAMES = frozenset({"FAQPage", "QAPage"})
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SOCIAL_HOST_RE = re.compile(
    r"(?:facebook|instagram|linkedin|twitter|x\.com|youtube|tiktok|wa\.me|whatsapp)\.",
    re.IGNORECASE,
)
_STYLE_PX_RE = re.compile(
    r"(?:^|;)\s*(width|height)\s*:\s*(\d+(?:\.\d+)?)px",
    re.IGNORECASE,
)
_CHROME_SELECTORS = "script, style, noscript, nav, footer, [role=navigation], [role=contentinfo]"


@dataclass
class PageObservations:
    """AEO/GEO-lite and rendering-need observations. Not scores."""

    question_headings: list[str] = field(default_factory=list)
    concise_answer_leads: int = 0
    authorship_signals: list[str] = field(default_factory=list)
    date_markup: list[str] = field(default_factory=list)
    outbound_reference_links: list[str] = field(default_factory=list)
    spa_shell: bool = False
    spa_shell_reason: str | None = None


@dataclass
class ExtractionResult:
    page: Page
    observations: PageObservations


def html_hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def extract_crawled_page(crawled: CrawledPage) -> ExtractionResult:
    html = crawled.body.decode("utf-8", errors="replace")
    x_robots = None
    for key, value in crawled.headers.items():
        if key.lower() == "x-robots-tag":
            x_robots = value
            break
    return extract_page(
        html,
        url=crawled.final_url or crawled.url,
        status_code=crawled.status_code,
        redirect_chain=list(crawled.redirect_chain),
        x_robots_tag=x_robots,
        sitemap_member=crawled.in_sitemap,
        crawl_depth=crawled.depth,
        discovery_source=crawled.discovery_source,
        fetch_ms=crawled.fetch_ms,
    )


def extract_page(
    html: str,
    *,
    url: str,
    status_code: int | None = None,
    redirect_chain: list[str] | None = None,
    x_robots_tag: str | None = None,
    sitemap_member: bool | None = None,
    crawl_depth: int | None = None,
    discovery_source: str | None = None,
    fetch_ms: int | None = None,
) -> ExtractionResult:
    tree = HTMLParser(html)
    origin = _safe_origin(url)

    title_node = tree.css_first("title")
    title = _text(title_node) or None

    meta_description = _meta_content(tree, name="description")
    canonical = _canonical(tree, url)
    robots_meta = _meta_content(tree, name="robots")
    viewport = _meta_content(tree, name="viewport")
    language = _attr(tree.css_first("html"), "lang")

    headings = _headings(tree)
    links = _links(tree, url, origin)
    images = _images(tree, url)
    structured = _json_ld(tree)
    open_graph = _property_metas(tree, "og:")
    twitter = _name_metas(tree, "twitter:")
    hreflang = _hreflang(tree, url)

    content, word_count = _visible_content(html)
    observations = _observations(tree, headings, links)

    questions = [
        Question(text=heading, answer=None) for heading in observations.question_headings
    ]
    # FAQPage/QAPage JSON-LD is authoritative for answers: match question
    # headings to structured-data questions by normalized text. Without this,
    # an accordion page whose answers render only via JS (or live only in the
    # markup's JSON-LD) reports every answer as missing.
    faq_pairs = _faq_answer_pairs(structured)
    faq_by_key = {_norm_question_key(text): answer for text, answer in faq_pairs}
    heading_keys = {_norm_question_key(question.text) for question in questions}
    for index, question in enumerate(questions):
        answer = faq_by_key.get(_norm_question_key(question.text))
        if answer is not None:
            questions[index] = Question(text=question.text, answer=answer)
    # Fall back to the first visible answer-lead paragraph for questions the
    # structured data did not answer.
    if observations.concise_answer_leads:
        lead = _first_answer_lead(tree)
        if lead:
            for index, question in enumerate(questions):
                if question.answer is None:
                    questions[index] = Question(text=question.text, answer=lead)
                    break
    # Structured-data questions with no matching heading still count as FAQ
    # content (and satisfy AEO-FAQ-SCHEMA-001) — append them.
    for text, answer in faq_pairs:
        if _norm_question_key(text) not in heading_keys:
            questions.append(Question(text=text, answer=answer))

    page = Page(
        url=url,
        title=title,
        meta_description=meta_description,
        canonical=canonical,
        robots=RobotsDirectives(meta=robots_meta, x_robots_tag=x_robots_tag),
        headings=headings,
        content=content,
        links=links,
        images=images,
        structured_data=structured,
        entities=[],
        questions=questions,
        status_code=status_code,
        redirect_chain=list(redirect_chain or []),
        language=language,
        hreflang=hreflang,
        open_graph=open_graph,
        twitter=twitter,
        viewport=viewport,
        word_count=word_count,
        sitemap_member=sitemap_member,
        crawl_depth=crawl_depth,
        discovery_source=discovery_source,
        raw_html_hash=html_hash(html),
        agent_tools=detect_agent_tools(html),
        fetch_ms=fetch_ms,
    )
    return ExtractionResult(page=page, observations=observations)


def _safe_origin(url: str) -> str | None:
    try:
        return origin_of(url)
    except Exception:
        return None


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return " ".join(node.text(separator=" ", strip=True).split())


def _attr(node: Node | None, name: str) -> str | None:
    if node is None:
        return None
    value = node.attributes.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _meta_content(tree: HTMLParser, *, name: str) -> str | None:
    for node in tree.css("meta"):
        attr_name = (node.attributes.get("name") or "").strip().lower()
        if attr_name == name.lower():
            content = node.attributes.get("content")
            if content is not None:
                return content.strip() or None
    return None


def _canonical(tree: HTMLParser, base: str) -> str | None:
    for node in tree.css("link"):
        rel = (node.attributes.get("rel") or "").lower().split()
        if "canonical" not in rel:
            continue
        href = node.attributes.get("href")
        if not href:
            return None
        return urljoin(base, href.strip())
    return None


def _headings(tree: HTMLParser) -> list[Heading]:
    found: list[Heading] = []
    root = tree.body or tree.root
    if root is None:
        return found
    for node in root.traverse():
        tag = (node.tag or "").lower()
        if len(tag) != 2 or tag[0] != "h":
            continue
        try:
            level = int(tag[1])
        except ValueError:
            continue
        text = _text(node)
        if text:
            found.append(Heading(level=level, text=text))
    return found


def _links(tree: HTMLParser, base: str, origin: str | None) -> list[Link]:
    links: list[Link] = []
    for node in tree.css("a[href], area[href]"):
        href = node.attributes.get("href")
        if not href:
            continue
        absolute = normalize_url(href, base=base)
        if absolute is None:
            continue
        internal = same_origin(absolute, origin) if origin else None
        links.append(
            Link(
                href=absolute,
                text=_text(node) or None,
                rel=(node.attributes.get("rel") or None),
                internal=internal,
            )
        )
    return links


def _images(tree: HTMLParser, base: str) -> list[Image]:
    images: list[Image] = []
    for node in tree.css("img"):
        src = node.attributes.get("src")
        if not src:
            continue
        alt_present = "alt" in node.attributes
        alt_value = node.attributes.get("alt") if alt_present else None
        images.append(
            Image(
                src=_resolve_image_src(src, base),
                alt=alt_value if alt_present else None,
                width=_dimension(node, "width"),
                height=_dimension(node, "height"),
            )
        )
    return images


def _resolve_image_src(src: str, base: str) -> str:
    """Unwrap Next.js `/_next/image?url=` optimizer URLs to the underlying asset."""

    absolute = urljoin(base, src.strip())
    parts = urlsplit(absolute)
    path = (parts.path or "").rstrip("/")
    if path != "/_next/image" and not path.endswith("/_next/image"):
        return absolute
    inner = parse_qs(parts.query).get("url", [None])[0]
    if not inner:
        return absolute
    decoded = unquote(inner)
    if decoded.startswith(("http://", "https://", "/")):
        return urljoin(base, decoded)
    return absolute


def _int_attr(node: Node, name: str) -> int | None:
    raw = node.attributes.get(name)
    if raw is None:
        return None
    try:
        value = int(float(raw.strip()))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _dimension(node: Node, name: str) -> int | None:
    attr = _int_attr(node, name)
    if attr is not None:
        return attr
    style = node.attributes.get("style") or ""
    for match in _STYLE_PX_RE.finditer(style):
        if match.group(1).lower() == name:
            try:
                value = int(float(match.group(2)))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def _json_ld(tree: HTMLParser) -> list[StructuredData]:
    blocks: list[StructuredData] = []
    for node in tree.css("script"):
        script_type = (node.attributes.get("type") or "").strip().lower()
        if script_type != "application/ld+json":
            continue
        raw = (node.text() or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            blocks.append(
                StructuredData(
                    format="json-ld",
                    type=None,
                    raw=raw,
                    parsed=None,
                    parse_error=str(exc.msg),
                )
            )
            continue
        for item in _json_ld_items(parsed):
            blocks.append(item)
    return blocks


def _json_ld_items(parsed: Any) -> list[StructuredData]:
    if isinstance(parsed, list):
        items: list[StructuredData] = []
        for element in parsed:
            items.extend(_json_ld_items(element))
        return items
    if not isinstance(parsed, dict):
        return [
            StructuredData(
                format="json-ld",
                type=None,
                raw=json.dumps(parsed),
                parsed=None,
                parse_error="JSON-LD root is not an object",
            )
        ]
    if isinstance(parsed.get("@graph"), list):
        items = _json_ld_items(parsed["@graph"])
        if items:
            return items
    types = parsed.get("@type")
    if isinstance(types, list):
        type_name = ",".join(str(item) for item in types)
    elif types is None:
        type_name = None
    else:
        type_name = str(types)
    return [
        StructuredData(
            format="json-ld",
            type=type_name,
            raw=json.dumps(parsed),
            parsed=parsed,
            parse_error=None,
        )
    ]


def _property_metas(tree: HTMLParser, prefix: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.css("meta"):
        prop = (node.attributes.get("property") or "").strip()
        if prop.lower().startswith(prefix):
            content = node.attributes.get("content")
            if content is not None:
                found[prop] = content
    return found


def _name_metas(tree: HTMLParser, prefix: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.css("meta"):
        name = (node.attributes.get("name") or "").strip()
        if name.lower().startswith(prefix):
            content = node.attributes.get("content")
            if content is not None:
                found[name] = content
    return found


def _hreflang(tree: HTMLParser, base: str) -> list[Hreflang]:
    found: list[Hreflang] = []
    for node in tree.css("link"):
        lang = node.attributes.get("hreflang")
        href = node.attributes.get("href")
        if not lang or not href:
            continue
        found.append(Hreflang(lang=lang.strip(), href=urljoin(base, href.strip())))
    return found


def _visible_content(html: str) -> tuple[str, int]:
    tree = HTMLParser(html)
    for node in tree.css(_CHROME_SELECTORS):
        node.decompose()
    body = tree.body
    text = _text(body) if body is not None else _text(tree.root)
    words = [part for part in text.split() if part]
    return text, len(words)


def _observations(
    tree: HTMLParser,
    headings: list[Heading],
    links: list[Link],
) -> PageObservations:
    question_headings = [
        heading.text for heading in headings if heading.level in (2, 3) and _QUESTION_RE.search(heading.text)
    ]
    lead = _first_answer_lead(tree)
    authorship: list[str] = []
    if _meta_content(tree, name="author"):
        authorship.append("author meta tag")
    if _property_metas(tree, "article:author"):
        authorship.append("article:author meta tag")
    for node in tree.css("a"):
        rel = (node.attributes.get("rel") or "").lower().split()
        if "author" in rel:
            authorship.append("author relationship link")
            break
    dates: list[str] = []
    if _property_metas(tree, "article:published_time") or _property_metas(
        tree, "article:modified_time"
    ):
        dates.append("article date metadata")
    if tree.css_first("time[datetime]"):
        dates.append("time datetime markup")
    outbound = [
        link.href
        for link in links
        if link.internal is False and not _SOCIAL_HOST_RE.search(link.href)
    ]
    spa_reason = _spa_shell_reason(tree)
    return PageObservations(
        question_headings=question_headings,
        concise_answer_leads=1 if lead else 0,
        authorship_signals=authorship,
        date_markup=dates,
        outbound_reference_links=outbound,
        spa_shell=spa_reason is not None,
        spa_shell_reason=spa_reason,
    )


def _first_answer_lead(tree: HTMLParser) -> str | None:
    """First visible paragraph following a question heading.

    20-120 words: a 25-word direct answer is a good lead, so requiring 40
    here made real answers look missing downstream.
    """

    for node in tree.css("h2, h3"):
        text = _text(node)
        if not _QUESTION_RE.search(text):
            continue
        sibling = node.next
        while sibling is not None and getattr(sibling, "tag", None) is None:
            sibling = sibling.next
        # Walk following siblings until a paragraph.
        cursor = node.next
        while cursor is not None:
            tag = getattr(cursor, "tag", None)
            if tag == "p":
                paragraph = _text(cursor)
                words = paragraph.split()
                if 20 <= len(words) <= 120:
                    return paragraph
                return None
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                return None
            cursor = cursor.next
    return None


def _norm_question_key(text: str) -> str:
    return " ".join(text.lower().split()).rstrip("?").strip()


def _faq_answer_pairs(blocks: list[StructuredData]) -> list[tuple[str, str]]:
    """(question, answer) pairs from FAQPage/QAPage JSON-LD blocks."""

    pairs: list[tuple[str, str]] = []
    for block in blocks:
        parsed = block.parsed
        if not isinstance(parsed, dict):
            continue
        type_name = block.type or ""
        tokens = {part.strip() for part in type_name.replace(",", " ").split()}
        raw_type = parsed.get("@type")
        if isinstance(raw_type, str):
            tokens.add(raw_type)
        elif isinstance(raw_type, list):
            tokens.update(str(item) for item in raw_type)
        if not tokens & _FAQ_TYPE_NAMES:
            continue
        entities = parsed.get("mainEntity")
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            question_text = _clean_faq_text(entity.get("name"))
            answer_text = _faq_answer_text(entity.get("acceptedAnswer") or entity.get("suggestedAnswer"))
            if question_text and answer_text:
                pairs.append((question_text, answer_text))
    return pairs


def _faq_answer_text(raw: Any) -> str | None:
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict):
        raw = raw.get("text")
    return _clean_faq_text(raw)


def _clean_faq_text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = _HTML_TAG_RE.sub(" ", raw)
    text = " ".join(text.split())
    return text or None


def _spa_shell_reason(tree: HTMLParser) -> str | None:
    for node_id in ("root", "app", "__next"):
        node = tree.css_first(f"#{node_id}")
        if node is not None and not _text(node):
            return "empty application root"
    script_srcs = [
        node for node in tree.css("script") if node.attributes.get("src")
    ]
    if len(script_srcs) >= 3:
        return "multiple client script bundles"
    if tree.css_first("noscript") is not None and tree.css_first("body") is not None:
        body_text = _text(tree.body)
        if len(body_text.split()) < 20:
            return "noscript fallback"
    return None


@dataclass
class LlmsTxtObservation:
    url: str
    state: str
    http_status: int


def classify_llms_text(text: str) -> str:
    if re.search(r"^\s*#\s+\S", text, re.MULTILINE):
        return "present"
    return "malformed"


def inspect_llms_txt(
    origin: str,
    *,
    settings=None,
    client=None,
    lookup=None,
) -> LlmsTxtObservation:
    """Fetch `{origin}/llms.txt`. Failure is a recorded state, not a crash."""

    from app.intelligence.website.fetch import FetchError, fetch_public

    url = origin.rstrip("/") + "/llms.txt"
    try:
        result = fetch_public(url, settings=settings, client=client, lookup=lookup)
    except FetchError:
        return LlmsTxtObservation(url=url, state="unavailable", http_status=0)
    if result.status_code == 404:
        return LlmsTxtObservation(url=url, state="missing", http_status=404)
    if result.status_code < 200 or result.status_code >= 300:
        return LlmsTxtObservation(
            url=url, state="unavailable", http_status=result.status_code
        )
    return LlmsTxtObservation(
        url=url,
        state=classify_llms_text(result.text),
        http_status=result.status_code,
    )
