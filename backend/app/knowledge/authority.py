"""Ranked authority scale for optimization knowledge sources (step 4.A.2).

Not every web source is equally trustworthy `[SPEC AGENTS.md §18, about-ArchitectOS.md
§20]`: official standards and vendor documentation (Google Search Central,
Schema.org, W3C/WHATWG) outrank independent blog posts, and content with no
verifiable source is rejected outright — never merely down-weighted.

The ingest job (step 4.B.2) is expected to call `validate_attribution` before
constructing an `OptimizationSource` or `OptimizationRule` row.
"""

from __future__ import annotations

import enum


class AuthorityLevel(str, enum.Enum):
    """Ranked from most to least authoritative. Ordering lives in `_RANK`, not
    declaration order, so the scale can be re-read without relying on enum
    member order.
    """

    OFFICIAL_STANDARD = "official_standard"
    """Standards bodies and spec owners: W3C, WHATWG, IETF, Schema.org itself."""

    OFFICIAL_VENDOR_DOCS = "official_vendor_docs"
    """The vendor whose behavior the rule describes: Google Search Central,
    web.dev/Lighthouse docs, Bing Webmaster docs, Search Console help center."""

    PEER_REVIEWED_RESEARCH = "peer_reviewed_research"
    """Published research with named methodology: academic papers, industry
    studies with disclosed data and sampling."""

    REPUTABLE_PRACTITIONER = "reputable_practitioner"
    """Editorially reviewed industry publications with named authorship and
    organizational accountability (e.g. established SEO trade publications)."""

    COMMUNITY_UNVERIFIED = "community_unverified"
    """An individual, attributed post (named author, working URL) with no
    editorial backing. The lowest rankable tier — still attributed, just not
    authoritative. Distinct from unattributed content, which is rejected
    entirely rather than placed at this level."""


_RANK: dict[AuthorityLevel, int] = {
    AuthorityLevel.OFFICIAL_STANDARD: 5,
    AuthorityLevel.OFFICIAL_VENDOR_DOCS: 4,
    AuthorityLevel.PEER_REVIEWED_RESEARCH: 3,
    AuthorityLevel.REPUTABLE_PRACTITIONER: 2,
    AuthorityLevel.COMMUNITY_UNVERIFIED: 1,
}


def rank(level: AuthorityLevel) -> int:
    """Numeric rank for `level`. Higher means more authoritative."""
    return _RANK[level]


def is_more_authoritative(a: AuthorityLevel, b: AuthorityLevel) -> bool:
    """True if `a` outranks `b` on the authority scale."""
    return rank(a) > rank(b)


def compare(a: AuthorityLevel, b: AuthorityLevel) -> int:
    """Standard three-way comparator (-1/0/1), for sorting evidence by authority."""
    return (rank(a) > rank(b)) - (rank(a) < rank(b))


class UnattributedSourceError(ValueError):
    """Raised when a knowledge record has no verifiable source attribution.

    This is a hard rejection, not a down-weighting: `[SPEC]` unattributed
    internet content must never enter the knowledge base at any authority
    level, including the lowest one.
    """


def validate_attribution(source: str | None, source_url: str | None) -> None:
    """Reject a knowledge record that has no verifiable attribution.

    Raises `UnattributedSourceError` with a human-readable reason when the
    source name is missing/blank, the source URL is missing/blank, or the
    URL is not a well-formed http(s) address. Callers (the ingest job, and
    anything constructing an `OptimizationSource`/`OptimizationRule`) must
    call this before persisting a record.
    """
    if not source or not source.strip():
        raise UnattributedSourceError(
            "rejected: no source name given — unattributed internet content is "
            "rejected, not down-weighted"
        )
    if not source_url or not source_url.strip():
        raise UnattributedSourceError(
            f"rejected: source {source!r} has no source_url — unattributed "
            "internet content is rejected, not down-weighted"
        )
    if not (source_url.startswith("http://") or source_url.startswith("https://")):
        raise UnattributedSourceError(
            f"rejected: source_url {source_url!r} for source {source!r} is not "
            "a valid http(s) URL"
        )
