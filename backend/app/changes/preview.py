"""Helpers for post-change browser/SEO validation against a patched preview.

Live crawled URLs are never the source of truth for "did this patch work?".
Playwright must hit the sandbox-served workspace; site-scoped rules must
see sibling pages, not a single extracted document.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.connectors.model import Page
from app.intelligence.website.crawler import normalize_url

SITE_SCOPED_CHECKS = frozenset(
    {
        "orphan_page",
        "broken_internal_link",
        "title_duplicate_across_pages",
        "meta_description_duplicate_across_pages",
        "exact_duplicate_content",
        "hreflang_not_reciprocal",
    }
)

_REVIEWER_DIFF_MAX_CHARS = 12_000


def page_key(url: str) -> str:
    return normalize_url(url) or url


def is_site_scoped_check(check: str | None) -> bool:
    return check in SITE_SCOPED_CHECKS


def rewrite_live_url_to_preview(live_url: str, preview_base: str) -> str:
    """Map a crawled public URL onto the sandbox preview origin, path-only."""

    path = urlsplit(live_url).path or "/"
    base = preview_base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def extra_sibling_urls(
    crawled_urls: list[str],
    targeted_urls: list[str],
    *,
    limit: int,
) -> list[str]:
    """Other crawled pages to re-render so layout/nav edits are visible.

    Targeted validation only follows Route nodes. A shared layout change
    creates inbound links from pages the graph walk may not have matched.
    """

    if limit <= 0:
        return []
    targeted = {page_key(url) for url in targeted_urls}
    extras: list[str] = []
    seen: set[str] = set()
    for url in crawled_urls:
        key = page_key(url)
        if key in targeted or key in seen:
            continue
        seen.add(key)
        extras.append(url)
        if len(extras) >= limit:
            break
    return extras


def overlay_pages(stored: list[Page], fresh: dict[str, Page]) -> list[Page]:
    """Replace stored crawl snapshots with freshly rendered patched pages."""

    fresh_by_key = {page_key(url): page for url, page in fresh.items()}
    seen: set[str] = set()
    out: list[Page] = []
    for page in list(fresh_by_key.values()) + stored:
        key = page_key(page.url)
        if key in seen:
            continue
        seen.add(key)
        replacement = fresh_by_key.get(key)
        out.append(replacement if replacement is not None else page)
    return out


def compose_diff_summary(
    *,
    notes: str,
    files: list[dict],
    max_chars: int = _REVIEWER_DIFF_MAX_CHARS,
) -> str:
    """Reviewer-facing text: notes + per-file change_summary + unified diff."""

    parts: list[str] = []
    stripped_notes = notes.strip()
    if stripped_notes:
        parts.append(stripped_notes)
    for item in files:
        path = str(item.get("file_path") or "")
        summary = str(item.get("change_summary") or "").strip()
        unified = str(item.get("unified_diff") or "").strip()
        block = [f"file: {path}"]
        if summary:
            block.append(f"change_summary: {summary}")
        if unified:
            block.append(unified)
        parts.append("\n".join(block))
    text = "\n\n".join(parts).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 14].rstrip() + "\n...[truncated]"
