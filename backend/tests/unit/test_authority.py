"""Authority ranking scale and attribution gate (step 4.A.2 verify)."""

import pytest

from app.knowledge.authority import (
    AuthorityLevel,
    UnattributedSourceError,
    compare,
    is_more_authoritative,
    rank,
    validate_attribution,
)


def test_official_standard_outranks_reputable_practitioner() -> None:
    assert is_more_authoritative(
        AuthorityLevel.OFFICIAL_STANDARD, AuthorityLevel.REPUTABLE_PRACTITIONER
    )


def test_official_vendor_docs_outranks_community_unverified() -> None:
    assert is_more_authoritative(
        AuthorityLevel.OFFICIAL_VENDOR_DOCS, AuthorityLevel.COMMUNITY_UNVERIFIED
    )


def test_rank_is_strictly_ordered_highest_to_lowest() -> None:
    ordered = [
        AuthorityLevel.OFFICIAL_STANDARD,
        AuthorityLevel.OFFICIAL_VENDOR_DOCS,
        AuthorityLevel.PEER_REVIEWED_RESEARCH,
        AuthorityLevel.REPUTABLE_PRACTITIONER,
        AuthorityLevel.COMMUNITY_UNVERIFIED,
    ]
    ranks = [rank(level) for level in ordered]
    assert ranks == sorted(ranks, reverse=True)


def test_compare_matches_rank_direction() -> None:
    assert compare(AuthorityLevel.OFFICIAL_STANDARD, AuthorityLevel.COMMUNITY_UNVERIFIED) == 1
    assert compare(AuthorityLevel.COMMUNITY_UNVERIFIED, AuthorityLevel.OFFICIAL_STANDARD) == -1
    assert compare(AuthorityLevel.PEER_REVIEWED_RESEARCH, AuthorityLevel.PEER_REVIEWED_RESEARCH) == 0


def test_validate_attribution_accepts_a_named_http_source() -> None:
    validate_attribution("Google Search Central", "https://developers.google.com/search")


def test_ingest_with_no_source_name_is_rejected_with_a_reason() -> None:
    with pytest.raises(UnattributedSourceError, match="unattributed"):
        validate_attribution(None, "https://example.com/post")


def test_ingest_with_blank_source_name_is_rejected() -> None:
    with pytest.raises(UnattributedSourceError, match="unattributed"):
        validate_attribution("   ", "https://example.com/post")


def test_ingest_with_no_source_url_is_rejected_with_a_reason() -> None:
    with pytest.raises(UnattributedSourceError, match="unattributed"):
        validate_attribution("Some Blog", None)


def test_ingest_with_non_http_source_url_is_rejected() -> None:
    with pytest.raises(UnattributedSourceError, match="not a valid http"):
        validate_attribution("Some Blog", "ftp://example.com/post")


def test_unattributed_content_is_rejected_not_downweighted() -> None:
    """Rejection is a hard error raised before any AuthorityLevel is assigned —
    unattributed content never lands at the lowest tier either."""
    with pytest.raises(UnattributedSourceError):
        validate_attribution("", "")
