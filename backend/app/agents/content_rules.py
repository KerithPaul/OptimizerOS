"""Content change rules (step 7.9, `[SPEC about-ArchitectOS.md §68]`).

Deterministic, not LLM-judged — the same "deterministic code for
validate" principle `[SPEC IMPLEMENTATION_PLAN_V2.md §1.5]` applies here:
an LLM should not be the sole judge of whether its own content edit is
safe. These are heuristic pre-write gates, not proof of a violation — a
false positive stops a change for human review, which matches
AGENTS.md §33's "if in doubt, STOP" posture better than a false negative
would.

Keyword stuffing is judged on reader-visible text. URLs, emails, HTML
markup, and JSON-LD identifier fields (`url`, `@id`, `sameAs`, `logo`)
are required by schema.org and are not stuffing.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

_STAT_RE = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s?%|\b\d[\d,]*(?:\.\d+)?\s*(?:million|billion|thousand|x\b)",
    re.IGNORECASE,
)
_ATTRIBUTION_RE = re.compile(
    r"\b(?:according to|studies show|research shows|experts (?:say|agree)|proven to|guaranteed to)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    "a an the of to in on for and or is are was were be been being this that "
    "with as by at from it its your our their his her they we you i".split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")

# A repeated word must appear this many times before frequency is even
# examined — short content naturally repeats a topic word a few times.
_MIN_REPEAT_COUNT = 6
_STUFFING_RATIO = 3.0

_LD_JSON_RE = re.compile(
    r"<script\b[^>]*\btype\s*=\s*['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_OR_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s\"'<>)\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CONTENT_JSON_KEYS = frozenset(
    {
        "name",
        "description",
        "headline",
        "text",
        "alternateName",
        "slogan",
        "abstract",
        "caption",
        "keywords",
        "articleBody",
        "about",
    }
)


@dataclass(frozen=True)
class ContentViolation:
    rule: str
    detail: str


def _strip_urls_and_emails(text: str) -> str:
    text = _URL_RE.sub(" ", text)
    return _EMAIL_RE.sub(" ", text)


def _json_ld_content_text(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _strip_urls_and_emails(raw)

    parts: list[str] = []

    def walk(node: object, parent_key: str | None = None) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, str(key))
        elif isinstance(node, list):
            for item in node:
                walk(item, parent_key)
        elif isinstance(node, str) and parent_key in _CONTENT_JSON_KEYS:
            parts.append(node)

    walk(data)
    return " ".join(parts)


def visible_text_for_content_rules(text: str) -> str:
    """Return the words a reader would see; drop URLs, markup, and schema ids."""

    def _ld_repl(match: re.Match[str]) -> str:
        return f" {_json_ld_content_text(match.group(1))} "

    text = _LD_JSON_RE.sub(_ld_repl, text)
    text = _SCRIPT_OR_STYLE_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    return _strip_urls_and_emails(text)


def _word_counts(text: str) -> Counter:
    visible = visible_text_for_content_rules(text)
    return Counter(
        word.lower() for word in _WORD_RE.findall(visible) if word.lower() not in _STOPWORDS
    )


def check_content_change(before: str, after: str) -> list[ContentViolation]:
    """Return heuristic violations of the content-change strategy, if any."""

    violations: list[ContentViolation] = []

    if before.strip() and not after.strip():
        violations.append(
            ContentViolation("content_removed", "existing content was replaced with nothing")
        )
        return violations

    visible_before = visible_text_for_content_rules(before)
    visible_after = visible_text_for_content_rules(after)

    new_stats = set(m.group(0) for m in _STAT_RE.finditer(visible_after)) - set(
        m.group(0) for m in _STAT_RE.finditer(visible_before)
    )
    if new_stats:
        violations.append(
            ContentViolation(
                "unsupported_statistic",
                f"new numeric claim(s) not present in the original content: {sorted(new_stats)}",
            )
        )

    new_attributions = set(m.group(0).lower() for m in _ATTRIBUTION_RE.finditer(visible_after)) - set(
        m.group(0).lower() for m in _ATTRIBUTION_RE.finditer(visible_before)
    )
    if new_attributions:
        violations.append(
            ContentViolation(
                "unsupported_attribution",
                f"new attributed/absolute claim(s) not present in the original content: "
                f"{sorted(new_attributions)}",
            )
        )

    before_counts = _word_counts(before)
    after_counts = _word_counts(after)
    for word, after_count in after_counts.items():
        if after_count < _MIN_REPEAT_COUNT:
            continue
        before_count = before_counts.get(word, 0)
        if before_count == 0 or (after_count / before_count) >= _STUFFING_RATIO:
            violations.append(
                ContentViolation(
                    "keyword_stuffing",
                    f"'{word}' appears {after_count} times (was {before_count})",
                )
            )

    return violations
