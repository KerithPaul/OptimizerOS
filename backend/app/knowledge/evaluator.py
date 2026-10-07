"""Deterministic rule evaluator (step 4.C.1).

Takes Common Website Model pages (plus optional crawl-run stats and
repository facts) and emits structured **rule hits**. A hit is not a
Finding: it is `{rule_id, rule_version, severity, affected_resource,
observed_value, expected_condition, source_url}`. Hypotheses, confidence,
mechanism, and risk are assembled in Phase 5.

Mechanical rules from `knowledge/` are executed here. `llm_interpreted`
rules are skipped — Phase 4 must not evaluate them `[SPEC]`. An unknown
page/crawl field or an unknown `check` is recorded and skipped; the
evaluator does not guess.

The same inputs evaluated twice produce byte-identical hits.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict

from app.connectors.model import Image, Page, StructuredData
from app.intelligence.website.crawler import normalize_url
from app.knowledge.rulefile import KNOWLEDGE_ROOT, RuleConditions, RuleFile, load_all_rule_files

logger = logging.getLogger("architectos.knowledge.evaluator")

# Catalog files have no version field; ingest assigns 1 on first insert.
UNINGESTED_RULE_VERSION = 1

_SkipReason = Literal[
    "unknown_field",
    "unknown_check",
    "llm_interpreted",
    "missing_context",
    "inapplicable",
]

_ARTICLE_TYPES = frozenset({"Article", "NewsArticle", "BlogPosting"})
_ENTITY_TYPES = frozenset({"Organization", "Person", "LocalBusiness"})
_FAQ_TYPES = frozenset({"FAQPage", "QAPage"})
_OG_REQUIRED = ("title", "type", "image", "url")
# ogp.me: og:image:url / og:image:secure_url are identical to og:image.
_OG_IMAGE_ALIASES = frozenset({"image:url", "image:secure_url"})
_SITEMAP_INVALID_STATES = frozenset({"invalid", "not_xml"})


class RuleHit(BaseModel):
    """One mechanical rule match. Not a Finding."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    rule_version: int
    severity: str
    affected_resource: str
    observed_value: Any
    expected_condition: str
    source_url: str


@dataclass(frozen=True)
class EvaluationSkip:
    """A rule the evaluator did not apply, with the reason it stopped."""

    rule_id: str
    reason: _SkipReason
    detail: str


@dataclass(frozen=True)
class EvaluationResult:
    hits: tuple[RuleHit, ...]
    skips: tuple[EvaluationSkip, ...]

    def hits_bytes(self) -> bytes:
        return hits_bytes(self.hits)


@dataclass(frozen=True)
class EvaluableRule:
    """A rule in the shape the evaluator runs, independent of YAML vs MySQL."""

    rule_id: str
    version: int
    severity: str
    source_url: str
    conditions: RuleConditions

    @classmethod
    def from_rule_file(cls, rule: RuleFile, *, version: int = UNINGESTED_RULE_VERSION) -> EvaluableRule:
        return cls(
            rule_id=rule.rule_id,
            version=version,
            severity=rule.severity.value,
            source_url=rule.source.source_url,
            conditions=rule.conditions,
        )

    @classmethod
    def from_row(cls, row: Any) -> EvaluableRule:
        severity = row.severity.value if isinstance(row.severity, Enum) else str(row.severity)
        return cls(
            rule_id=row.rule_id,
            version=int(row.version),
            severity=severity,
            source_url=row.source_url,
            conditions=RuleConditions.model_validate(row.conditions),
        )


@dataclass
class _Ctx:
    pages: tuple[Page, ...]
    crawl_stats: Mapping[str, Any] | None
    repository_facts: Mapping[str, Any] | None
    skips: list[EvaluationSkip] = field(default_factory=list)

    def skip(self, rule_id: str, reason: _SkipReason, detail: str) -> None:
        self.skips.append(EvaluationSkip(rule_id=rule_id, reason=reason, detail=detail))
        logger.info("evaluator skip rule_id=%s reason=%s detail=%s", rule_id, reason, detail)


_CheckFn = Callable[[EvaluableRule, _Ctx], list[RuleHit]]
_CHECKS: dict[str, _CheckFn] = {}


def _check(name: str) -> Callable[[_CheckFn], _CheckFn]:
    def decorator(fn: _CheckFn) -> _CheckFn:
        _CHECKS[name] = fn
        return fn

    return decorator


def catalog_rules(*, version: int = UNINGESTED_RULE_VERSION) -> list[EvaluableRule]:
    return [
        EvaluableRule.from_rule_file(item.rule, version=version)
        for item in load_all_rule_files(KNOWLEDGE_ROOT)
    ]


def merge_evaluable_rules(
    db_rules: Sequence[EvaluableRule],
    *,
    catalog: Sequence[EvaluableRule] | None = None,
) -> list[EvaluableRule]:
    """Ingested MySQL versions win; on-disk catalog fills rules not yet ingested.

    Audits that only load MySQL miss YAML files added after the last ingest.
    Catalog-only evaluation is the fallback when the database is empty.
    """

    merged = {rule.rule_id: rule for rule in (catalog if catalog is not None else catalog_rules())}
    for rule in db_rules:
        merged[rule.rule_id] = rule
    return list(merged.values())


def is_mechanically_executable(conditions: RuleConditions) -> bool:
    """True when this evaluator can run the condition without an LLM."""

    if conditions.evaluation != "mechanical":
        return False
    if conditions.check in _CHECKS:
        return True
    return conditions.field is not None and conditions.operator is not None


def hits_bytes(hits: Sequence[RuleHit]) -> bytes:
    """Canonical encoding used to verify byte-identical re-evaluation."""

    payload = [hit.model_dump(mode="json") for hit in hits]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def evaluate_page(
    page: Page,
    *,
    crawl_stats: Mapping[str, Any] | None = None,
    repository_facts: Mapping[str, Any] | None = None,
    sibling_pages: Sequence[Page] | None = None,
    rules: Sequence[EvaluableRule] | None = None,
) -> EvaluationResult:
    """Evaluate one page. Pass `sibling_pages` for site-scoped checks.

    Sibling pages with the same normalized URL as `page` are dropped so a
    crawl snapshot of the same resource cannot double-count the target.
    """

    pages: list[Page] = [page]
    seen = {normalize_url(page.url) or page.url}
    if sibling_pages:
        for sibling in sibling_pages:
            key = normalize_url(sibling.url) or sibling.url
            if key in seen:
                continue
            seen.add(key)
            pages.append(sibling)
    return evaluate(
        pages,
        crawl_stats=crawl_stats,
        repository_facts=repository_facts,
        rules=rules,
    )


def evaluate(
    pages: Page | Sequence[Page],
    *,
    crawl_stats: Mapping[str, Any] | None = None,
    repository_facts: Mapping[str, Any] | None = None,
    rules: Sequence[EvaluableRule] | None = None,
) -> EvaluationResult:
    """Run every mechanical catalog rule against the provided pages.

    `repository_facts` is accepted so callers can pass profiler/AST facts
    without a second API; the current catalog has no mechanical check that
    reads them.
    """

    page_list = (pages,) if isinstance(pages, Page) else tuple(pages)
    ordered = tuple(sorted(page_list, key=lambda page: page.url))
    ctx = _Ctx(pages=ordered, crawl_stats=crawl_stats, repository_facts=repository_facts)
    evaluable = list(rules) if rules is not None else catalog_rules()

    hits: list[RuleHit] = []
    for rule in evaluable:
        cond = rule.conditions
        if cond.evaluation == "llm_interpreted":
            ctx.skip(rule.rule_id, "llm_interpreted", cond.check)
            continue
        handler = _CHECKS.get(cond.check)
        if handler is not None:
            hits.extend(handler(rule, ctx))
            continue
        if cond.field is not None and cond.operator is not None:
            hits.extend(_generic(rule, ctx))
            continue
        ctx.skip(rule.rule_id, "unknown_check", cond.check)

    ranked = _sort_hits(hits)
    return EvaluationResult(hits=tuple(ranked), skips=tuple(ctx.skips))


def _sort_hits(hits: Sequence[RuleHit]) -> list[RuleHit]:
    return sorted(
        hits,
        key=lambda hit: (
            hit.rule_id,
            hit.affected_resource,
            json.dumps(hit.observed_value, sort_keys=True, separators=(",", ":"), default=str),
            hit.expected_condition,
        ),
    )


def _hit(
    rule: EvaluableRule,
    affected_resource: str,
    observed_value: Any,
    expected_condition: str,
) -> RuleHit:
    return RuleHit(
        rule_id=rule.rule_id,
        rule_version=rule.version,
        severity=rule.severity,
        affected_resource=affected_resource,
        observed_value=_jsonish(observed_value),
        expected_condition=expected_condition,
        source_url=rule.source_url,
    )


def _jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonish(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonish(item) for item in value]
    return str(value)


def _norm(url: str | None) -> str | None:
    if not url:
        return None
    return normalize_url(url) or url


def _is_site_root(url: str) -> bool:
    """The origin root is discoverable as the site's address, not an orphan.

    Path `/` (with or without a trailing slash after normalisation) is the
    homepage. `/index.html` is a document URL and is still evaluated.
    """

    path = urlsplit(url).path or "/"
    return path == "/"


# --- field resolution + operators ------------------------------------------------


def _dotted(obj: Any, path: str) -> tuple[bool, Any]:
    current = obj
    for part in path.split("."):
        if current is None:
            return True, None
        if isinstance(current, BaseModel):
            if part not in type(current).model_fields:
                return False, None
            current = getattr(current, part)
        elif isinstance(current, Mapping):
            if part not in current:
                return False, None
            current = current[part]
        else:
            return False, None
    return True, current


def _resolve_crawl(stats: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    """Resolve a crawl-run field against persist-shaped `stats_json`.

    Rule files address `stats_json.crawl.*`. The crawl job stores the crawl
    object at the top level of `stats_json` (no `crawl` wrapper). The first
    lookup wraps persist stats so the YAML path resolves.
    """

    wrapped = {"stats_json": {"crawl": dict(stats)}}
    found, value = _dotted(wrapped, field)
    if found:
        return True, value
    for prefix in ("stats_json.crawl.", "stats_json.", "crawl."):
        if field.startswith(prefix):
            found, value = _dotted(stats, field[len(prefix) :])
            if found:
                return True, value
    return _dotted(stats, field)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _apply_operator(operator: str, actual: Any, expected: Any) -> bool | None:
    """True/False when the operator can be applied; None when it cannot."""

    if operator == "is_null":
        return _is_blank(actual)
    if operator == "not_null":
        return not _is_blank(actual)
    if operator in {"equals", "not_equals"}:
        match = actual == expected
        return (not match) if operator == "not_equals" else match
    if operator in {"gt", "gte", "lt", "lte"}:
        if actual is None or expected is None:
            return None
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return None
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        return left <= right
    if operator in {"length_gt", "length_lt"}:
        if actual is None or not hasattr(actual, "__len__"):
            return None
        try:
            bound = int(expected)
        except (TypeError, ValueError):
            return None
        length = len(actual)
        return length > bound if operator == "length_gt" else length < bound
    if operator in {"count_gt", "count_eq", "count_lt"}:
        if actual is None or not isinstance(actual, (list, tuple, dict, set, str)):
            return None
        try:
            bound = int(expected)
        except (TypeError, ValueError):
            return None
        count = len(actual)
        if operator == "count_gt":
            return count > bound
        if operator == "count_eq":
            return count == bound
        return count < bound
    if operator in {"contains", "not_contains"}:
        if actual is None:
            return None
        try:
            contained = expected in actual
        except TypeError:
            return None
        return (not contained) if operator == "not_contains" else contained
    if operator == "regex_match":
        if actual is None or not isinstance(expected, str):
            return None
        return re.search(expected, str(actual)) is not None
    if operator in {"in", "not_in"}:
        if not isinstance(expected, (list, tuple, set)):
            return None
        contained = actual in expected
        return (not contained) if operator == "not_in" else contained
    return None


def _expected_generic(cond: RuleConditions) -> str:
    parts = [cond.field or cond.check, cond.operator or ""]
    if cond.value is not None:
        parts.append(json.dumps(cond.value, sort_keys=True, default=str))
    return " ".join(part for part in parts if part)


def _generic(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    cond = rule.conditions
    field_name = cond.field
    operator = cond.operator
    if field_name is None or operator is None:
        ctx.skip(rule.rule_id, "unknown_check", cond.check)
        return []

    if cond.applies_to == "page":
        root = field_name.split(".", 1)[0]
        if root not in Page.model_fields:
            ctx.skip(rule.rule_id, "unknown_field", field_name)
            return []
        hits: list[RuleHit] = []
        for page in ctx.pages:
            found, actual = _dotted(page, field_name)
            if not found:
                ctx.skip(rule.rule_id, "unknown_field", f"{page.url} {field_name}")
                continue
            matched = _apply_operator(operator, actual, cond.value)
            if matched is None:
                ctx.skip(rule.rule_id, "inapplicable", f"{page.url} {field_name} {operator}")
                continue
            if matched:
                hits.append(_hit(rule, page.url, actual, _expected_generic(cond)))
        return hits

    if cond.applies_to == "crawl_run":
        if ctx.crawl_stats is None:
            ctx.skip(rule.rule_id, "missing_context", field_name)
            return []
        found, actual = _resolve_crawl(ctx.crawl_stats, field_name)
        if not found:
            ctx.skip(rule.rule_id, "unknown_field", field_name)
            return []
        matched = _apply_operator(operator, actual, cond.value)
        if matched is None:
            ctx.skip(rule.rule_id, "inapplicable", f"{field_name} {operator}")
            return []
        if matched:
            return [_hit(rule, "crawl_run", actual, _expected_generic(cond))]
        return []

    ctx.skip(rule.rule_id, "unknown_check", f"{cond.applies_to}:{cond.check}")
    return []


# --- named mechanical checks -----------------------------------------------------


@_check("canonical_missing")
@_check("title_missing")
@_check("title_length_likely_truncated")
@_check("meta_description_missing")
@_check("meta_description_too_long")
@_check("lang_missing")
@_check("viewport_missing")
@_check("content_thin")
@_check("crawled_but_not_in_sitemap")
@_check("redirect_chain_multi_hop")
@_check("robots_txt_unavailable")
@_check("ai_bot_disallowed")
def _generic_named(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    return _generic(rule, ctx)


@_check("canonical_cross_domain")
def _canonical_cross_domain(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if _is_blank(page.canonical):
            continue
        page_dom = _registrable_domain(page.url)
        canon_dom = _registrable_domain(page.canonical)
        if page_dom is None or canon_dom is None:
            ctx.skip(rule.rule_id, "inapplicable", page.url)
            continue
        if page_dom != canon_dom:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"url_domain": page_dom, "canonical_domain": canon_dom, "canonical": page.canonical},
                    "canonical registrable domain equals page registrable domain",
                )
            )
    return hits


@_check("title_duplicate_across_pages")
def _title_duplicate(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    return _duplicate_text_hits(rule, ctx, field_name="title", expected="title unique across crawl")


@_check("meta_description_duplicate_across_pages")
def _metadesc_duplicate(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    return _duplicate_text_hits(
        rule, ctx, field_name="meta_description", expected="meta_description unique across crawl"
    )


def _duplicate_text_hits(rule: EvaluableRule, ctx: _Ctx, *, field_name: str, expected: str) -> list[RuleHit]:
    groups: dict[str, list[Page]] = {}
    for page in ctx.pages:
        text = getattr(page, field_name)
        if _is_blank(text):
            continue
        key = str(text).strip().casefold()
        groups.setdefault(key, []).append(page)
    hits: list[RuleHit] = []
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        urls = [page.url for page in members]
        observed_text = getattr(members[0], field_name)
        for page in members:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {field_name: observed_text, "urls": urls},
                    expected,
                )
            )
    return hits


@_check("h1_missing")
def _h1_missing(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        h1s = [heading.text for heading in page.headings if heading.level == 1]
        if not h1s:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    [heading.level for heading in page.headings],
                    "headings contains a level-1 entry",
                )
            )
    return hits


@_check("h1_multiple")
def _h1_multiple(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        h1s = [heading.text for heading in page.headings if heading.level == 1]
        if len(h1s) > 1:
            hits.append(_hit(rule, page.url, h1s, "headings contains at most one level-1 entry"))
    return hits


@_check("heading_level_skip")
def _heading_level_skip(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        levels = [heading.level for heading in page.headings]
        if _has_heading_skip(levels):
            hits.append(
                _hit(rule, page.url, levels, "heading levels increase by at most 1")
            )
    return hits


def _has_heading_skip(levels: list[int]) -> bool:
    """True when a heading jumps more than one level past its predecessor.

    Document-order consecutive skips (H2 then H4) match WCAG outline
    guidance and how site-audit tools report "heading levels skip". A
    later H4 after an earlier H3 is still a skip if the immediately
    previous heading was an H2.
    """

    if len(levels) < 2:
        return False
    previous = levels[0]
    for level in levels[1:]:
        if level > previous + 1:
            return True
        previous = level
    return False


@_check("image_missing_alt")
def _image_missing_alt(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        seen: set[str] = set()
        for image in page.images:
            if _is_non_content_image(image):
                continue
            if image.alt is None or image.alt == "":
                if image.src in seen:
                    continue
                seen.add(image.src)
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} img:{image.src}",
                        {"src": image.src, "alt": image.alt},
                        "image.alt is non-empty",
                    )
                )
    return hits


@_check("image_missing_dimensions")
def _image_missing_dimensions(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        seen: set[str] = set()
        for image in page.images:
            if _is_non_content_image(image):
                continue
            if image.width is None or image.height is None:
                if image.src in seen:
                    continue
                seen.add(image.src)
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} img:{image.src}",
                        {"src": image.src, "width": image.width, "height": image.height},
                        "image.width and image.height are set",
                    )
                )
    return hits


def _is_non_content_image(image: Image) -> bool:
    """Skip tracking pixels and data-URI placeholders; they are not editorial images."""

    src = (image.src or "").strip()
    if not src or src.startswith("data:"):
        return True
    if image.width == 1 and image.height == 1:
        return True
    return False


@_check("broken_internal_link")
def _broken_internal_link(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    error_urls = {_norm(page.url) for page in ctx.pages if (page.status_code or 0) >= 400}
    error_urls.discard(None)
    error_urls |= _failed_url_set(ctx)
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for link in page.links:
            if link.internal is not True:
                continue
            href = _norm(link.href)
            if href is None:
                continue
            if _is_infra_internal_href(href):
                continue
            if href in error_urls:
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} -> {href}",
                        {"href": href, "status_or_failed": True},
                        "internal href is not a 4xx/failed URL",
                    )
                )
    return hits


def _is_infra_internal_href(href: str) -> bool:
    """Cloudflare and similar CDN/infra paths are not editorial internal links.

    `/cdn-cgi/l/email-protection` is injected at the edge; it is same-host so
    the crawler marks it internal, then 4xx/fail would otherwise fire a
    false-positive broken-link finding.
    """

    path = urlsplit(href).path or ""
    if not path.startswith("/"):
        path = "/" + path
    return path == "/cdn-cgi" or path.startswith("/cdn-cgi/")


def _failed_url_set(ctx: _Ctx) -> set[str]:
    if ctx.crawl_stats is None:
        return set()
    found, value = _resolve_crawl(ctx.crawl_stats, "stats_json.crawl.failed_urls")
    if not found or not isinstance(value, list):
        return set()
    out: set[str] = set()
    for item in value:
        if isinstance(item, str):
            normalized = _norm(item)
            if normalized:
                out.add(normalized)
    return out


@_check("exact_duplicate_content")
def _exact_duplicate_content(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    groups: dict[str, list[Page]] = {}
    for page in ctx.pages:
        digest = page.raw_html_hash
        if _is_blank(digest):
            continue
        groups.setdefault(digest, []).append(page)
    hits: list[RuleHit] = []
    for digest in sorted(groups):
        members = groups[digest]
        if len(members) < 2:
            continue
        for page in members:
            others = [other for other in members if other.url != page.url]
            if any(_is_canonical_alternate(page, other) for other in others):
                continue
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"raw_html_hash": digest, "urls": [member.url for member in members]},
                    "raw_html_hash unique, or canonical alternate declared",
                )
            )
    return hits


def _is_canonical_alternate(left: Page, right: Page) -> bool:
    left_url, right_url = _norm(left.url), _norm(right.url)
    left_can, right_can = _norm(left.canonical), _norm(right.canonical)
    if left_url and right_can == left_url:
        return True
    if right_url and left_can == right_url:
        return True
    return False


@_check("hreflang_not_reciprocal")
def _hreflang_not_reciprocal(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    by_url = {_norm(page.url): page for page in ctx.pages}
    hits: list[RuleHit] = []
    for page in ctx.pages:
        origin = _norm(page.url)
        if origin is None:
            continue
        for alt in page.hreflang:
            target_url = _norm(alt.href)
            if target_url is None:
                continue
            target = by_url.get(target_url)
            if target is None:
                continue
            back = {_norm(entry.href) for entry in target.hreflang}
            if origin not in back:
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} hreflang:{target_url}",
                        {"lang": alt.lang, "href": alt.href},
                        "target page lists a reciprocal hreflang back to this URL",
                    )
                )
    return hits


@_check("noindex_on_sitemap_page")
def _noindex_on_sitemap_page(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if page.sitemap_member is not True:
            continue
        tokens = set()
        if page.robots is not None:
            tokens |= _robots_tokens(page.robots.meta)
            tokens |= _robots_tokens(page.robots.x_robots_tag)
        if "noindex" in tokens:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"sitemap_member": True, "robots_tokens": sorted(tokens)},
                    "sitemap member is not noindex",
                )
            )
    return hits


def _robots_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().casefold() for part in value.replace(",", " ").split() if part.strip()}


@_check("open_graph_incomplete")
def _open_graph_incomplete(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if not page.open_graph:
            continue
        present = _og_bare_keys(page.open_graph)
        missing = [key for key in _OG_REQUIRED if key not in present]
        if missing:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"present": sorted(present), "missing": missing},
                    "open_graph includes title, type, image, url",
                )
            )
    return hits


def _og_bare_keys(open_graph: Mapping[str, str]) -> set[str]:
    keys: set[str] = set()
    for raw, value in open_graph.items():
        if not str(value or "").strip():
            continue
        key = str(raw).strip().casefold()
        keys.add(key)
        if key.startswith("og:"):
            bare = key[3:]
            keys.add(bare)
            if bare in _OG_IMAGE_ALIASES:
                keys.add("image")
        elif key in _OG_IMAGE_ALIASES:
            keys.add("image")
    return keys


@_check("orphan_page")
def _orphan_page(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    inbound: dict[str, int] = {(_norm(page.url) or page.url): 0 for page in ctx.pages}
    known = set(inbound)
    for page in ctx.pages:
        source = _norm(page.url) or page.url
        for link in page.links:
            if link.internal is not True:
                continue
            dest = _norm(link.href)
            if dest is None or dest == source or dest not in known:
                continue
            inbound[dest] += 1
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if _is_site_root(page.url):
            continue
        key = _norm(page.url) or page.url
        if inbound.get(key, 0) == 0:
            hits.append(_hit(rule, page.url, {"inbound_internal_links": 0}, "at least one inbound internal link"))
    return hits


@_check("sitemap_invalid")
def _sitemap_invalid(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    if ctx.crawl_stats is None:
        ctx.skip(rule.rule_id, "missing_context", "stats_json.crawl.sitemap.records")
        return []
    found, records = _resolve_crawl(ctx.crawl_stats, "stats_json.crawl.sitemap.records")
    if not found:
        ctx.skip(rule.rule_id, "unknown_field", "stats_json.crawl.sitemap.records")
        return []
    if not isinstance(records, list):
        ctx.skip(rule.rule_id, "inapplicable", "sitemap.records is not a list")
        return []
    hits: list[RuleHit] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        parse_state = record.get("parse_state")
        if parse_state in _SITEMAP_INVALID_STATES:
            url = str(record.get("url") or "sitemap")
            hits.append(
                _hit(
                    rule,
                    url,
                    {"parse_state": parse_state, "kind": record.get("kind")},
                    "sitemap parse_state is not invalid or not_xml",
                )
            )
    return hits


@_check("structured_data_parse_error")
def _structured_data_parse_error(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for index, block in enumerate(page.structured_data):
            if block.parse_error is not None:
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} structured_data[{index}]",
                        {"parse_error": block.parse_error, "type": block.type},
                        "structured_data.parse_error is null",
                    )
                )
    return hits


@_check("structured_data_absent")
def _structured_data_absent(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if not page.structured_data:
            hits.append(_hit(rule, page.url, [], "structured_data is non-empty"))
    return hits


@_check("url_structure_complex")
def _url_structure_complex(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        parts = urlsplit(page.url)
        params = parse_qsl(parts.query, keep_blank_values=True)
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(params) > 3 or len(segments) > 6:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"query_params": len(params), "path_segments": len(segments)},
                    "query params <= 3 and path segments <= 6",
                )
            )
    return hits


# Words too generic to be a keyword candidate. Intersection with an
# editorial stoplist is intentional: "services", "home", "menu" dominate TF
# on almost every site template and are useless as focus terms.
_KEYWORD_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "nor", "for", "of", "in",
        "on", "at", "by", "to", "as", "is", "are", "was", "be", "it",
        "its", "we", "our", "us", "you", "i", "not", "no", "all", "any",
        "about", "also", "been", "being", "best", "both", "call", "come",
        "contact", "could", "does", "done", "each", "every", "from", "get",
        "have", "has", "had", "here", "home", "into", "just", "know", "like", "made",
        "make", "more", "most", "much", "need", "only", "over", "page",
        "read", "same", "shall", "should", "since", "site", "some", "such",
        "take", "than", "that", "their", "them", "then", "there", "these",
        "they", "this", "those", "through", "time", "under", "very", "want",
        "well", "were", "what", "when", "where", "which", "while", "will",
        "with", "your", "yours", "menu", "skip", "search", "click", "learn",
        "clicking", "please", "using", "used", "use", "one", "two", "new",
    }
)


def _keyword_candidates(text: str, *, limit: int = 6) -> list[str]:
    """Dominant unigrams + bigrams of `text`, by frequency.

    Deterministic: pure function of the page content. Bigrams usually name
    the real keyword ("criminal defense") better than the bare head noun.
    """

    words = re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower())
    tokens = [w for w in words if w not in _KEYWORD_STOPWORDS]
    freq: dict[str, int] = {}
    for token in tokens:
        freq[token] = freq.get(token, 0) + 1
    for left, right in zip(tokens, tokens[1:]):
        bigram = f"{left} {right}"
        freq[bigram] = freq.get(bigram, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[str] = []
    for term, _count in ranked:
        if any(term in chosen for chosen in out):
            continue  # "law" subsumed by "criminal law" already picked
        out.append(term)
        if len(out) >= limit:
            break
    return out


def _contains_term(haystack: str, term: str) -> bool:
    return term.lower() in " ".join(haystack.lower().split())


@_check("keyword_focus")
def _keyword_focus(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    """Dominant on-page terms missing from title AND meta description.

    Purely informational about which terms the page actually emphasizes;
    fires only when a dominant term is absent from both fields, which is
    the actionable case. Keyword extraction itself has no ground truth —
    that is why the rule's confidence is low.
    """

    hits: list[RuleHit] = []
    for page in ctx.pages:
        if page.word_count is None or page.word_count < 250:
            continue
        candidates = _keyword_candidates(page.content or "")
        if not candidates:
            continue
        title = page.title or ""
        description = page.meta_description or ""
        missing = [
            term
            for term in candidates
            if not _contains_term(title, term) and not _contains_term(description, term)
        ]
        if not missing:
            continue
        hits.append(
            _hit(
                rule,
                page.url,
                {
                    "dominant_terms": candidates,
                    "missing_from_title_and_description": missing,
                    "title": title,
                },
                "page's dominant terms appear in the title or meta description",
            )
        )
    return hits


@_check("answer_lead_missing_or_verbose")
def _answer_lead_missing_or_verbose(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for index, question in enumerate(page.questions):
            if question.text is None:
                continue
            words = 0 if question.answer is None else len(question.answer.split())
            # None → genuinely missing. Present answers only fire when far
            # outside the concise-lead band (20-160): a 30-word direct
            # answer is good AEO, not a defect.
            if question.answer is None or not (20 <= words <= 160):
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} questions[{index}]",
                        {"text": question.text, "answer_words": None if question.answer is None else words},
                        "question.answer present and roughly 20-160 words",
                    )
                )
    return hits


@_check("authorship_date_markup_missing")
def _authorship_date_markup_missing(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for index, block in enumerate(page.structured_data):
            types = _type_tokens(block)
            if not (types & _ARTICLE_TYPES):
                continue
            parsed = block.parsed
            if not isinstance(parsed, dict):
                continue
            if "author" not in parsed and "datePublished" not in parsed:
                hits.append(
                    _hit(
                        rule,
                        f"{page.url} structured_data[{index}]",
                        {"type": block.type, "keys": sorted(parsed)},
                        "Article structured data includes author or datePublished",
                    )
                )
    return hits


@_check("faq_schema_absent_with_faq_content")
def _faq_schema_absent_with_faq_content(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        answered = [question for question in page.questions if question.answer is not None]
        if len(answered) < 2:
            continue
        types: set[str] = set()
        for block in page.structured_data:
            types |= _type_tokens(block)
        if not (types & _FAQ_TYPES):
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"answered_questions": len(answered), "structured_data_types": sorted(types)},
                    "FAQ content has FAQPage or QAPage structured data",
                )
            )
    return hits


@_check("no_question_headings")
def _no_question_headings(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        question_headings = [
            heading.text
            for heading in page.headings
            if heading.level >= 2 and heading.text.rstrip().endswith("?")
        ]
        if not question_headings and not page.questions:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"headings": [heading.text for heading in page.headings], "questions": []},
                    "a level>=2 heading ends with ? or questions is non-empty",
                )
            )
    return hits


@_check("entity_markup_absent")
def _entity_markup_absent(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if page.word_count is None or page.word_count < 250:
            continue
        types: set[str] = set()
        for block in page.structured_data:
            types |= _type_tokens(block)
        if not page.entities and not (types & _ENTITY_TYPES):
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"word_count": page.word_count, "entities": 0, "structured_data_types": sorted(types)},
                    "entities non-empty or Organization/Person/LocalBusiness markup present",
                )
            )
    return hits


@_check("no_external_references")
def _no_external_references(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if page.word_count is None or page.word_count < 400:
            continue
        external = [link.href for link in page.links if link.internal is False]
        if not external:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"word_count": page.word_count, "external_links": 0},
                    "at least one outbound (internal=false) link",
                )
            )
    return hits


@_check("structured_data_density_low")
def _structured_data_density_low(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if page.word_count is None or page.word_count < 800:
            continue
        if not page.structured_data:
            hits.append(
                _hit(
                    rule,
                    page.url,
                    {"word_count": page.word_count, "structured_data": 0},
                    "long page has at least one structured_data block",
                )
            )
    return hits


def _type_tokens(block: StructuredData) -> set[str]:
    names: set[str] = set()
    if block.type:
        for part in block.type.replace(",", " ").split():
            names.add(part.rsplit("/", 1)[-1])
    parsed = block.parsed
    if isinstance(parsed, dict):
        raw = parsed.get("@type")
        items = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        for item in items:
            names.add(str(item).rsplit("/", 1)[-1])
    return {name.strip() for name in names if name.strip()}


def _agent_tool_resource(page: Page, name: str) -> str:
    return f"{page.url}#{name}"


def _agent_tool_payload(page: Page) -> list[dict[str, Any]]:
    return [tool.model_dump() for tool in page.agent_tools]


@_check("agent_tools_discovered")
def _agent_tools_discovered(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        if not page.agent_tools:
            continue
        hits.append(
            _hit(
                rule,
                page.url,
                {"tools": _agent_tool_payload(page)},
                "page exposes at least one WebMCP tool",
            )
        )
    return hits


@_check("agent_tool_description_missing")
def _agent_tool_description_missing(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for tool in page.agent_tools:
            if not tool.description_missing:
                continue
            hits.append(
                _hit(
                    rule,
                    _agent_tool_resource(page, tool.name),
                    {"name": tool.name, "registration": tool.registration, "description": tool.description},
                    "tool description is a non-empty string",
                )
            )
    return hits


@_check("agent_tool_input_schema_missing")
def _agent_tool_input_schema_missing(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for tool in page.agent_tools:
            if tool.input_schema:
                continue
            hits.append(
                _hit(
                    rule,
                    _agent_tool_resource(page, tool.name),
                    {"name": tool.name, "registration": tool.registration, "input_schema": False},
                    "tool has an input schema",
                )
            )
    return hits


@_check("agent_tool_annotation_missing")
def _agent_tool_annotation_missing(rule: EvaluableRule, ctx: _Ctx) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for page in ctx.pages:
        for tool in page.agent_tools:
            if tool.semantic_annotation:
                continue
            hits.append(
                _hit(
                    rule,
                    _agent_tool_resource(page, tool.name),
                    {
                        "name": tool.name,
                        "registration": tool.registration,
                        "semantic_annotation": False,
                    },
                    "tool sets readOnlyHint, untrustedContentHint, or consequentialHint",
                )
            )
    return hits


def _registrable_domain(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) < 2:
        return host
    return ".".join(labels[-2:])
