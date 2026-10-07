"""Capability report and mode validator (step 3.A.3 verify)."""

import pytest

from app.connectors.capabilities import (
    ModificationLevel,
    ModeNotAllowed,
    UnknownPlatform,
    allowed_modes,
    assert_mode_allowed,
    git_repository_capabilities,
    project_capability_report,
    report_for_platform,
    url_only_capabilities,
)
from app.models.project import ProjectMode


def test_url_only_report_claims_no_modification() -> None:
    report = url_only_capabilities()
    assert report.platform == "url_only"
    assert report.allows_modification() is False
    assert report.snapshot is False
    assert report.automatic_rollback is ModificationLevel.NONE
    assert report.source_access is False
    assert report.content_access is True
    assert report.metadata_access is True


def test_url_only_forbids_apply_locally() -> None:
    report = url_only_capabilities()
    with pytest.raises(ModeNotAllowed, match="APPLY_LOCALLY") as exc:
        assert_mode_allowed(ProjectMode.APPLY_LOCALLY, report)
    assert "SUGGEST_ONLY" in str(exc.value)


def test_url_only_allows_audit_and_suggest() -> None:
    report = url_only_capabilities()
    assert_mode_allowed(ProjectMode.AUDIT_ONLY, report)
    assert_mode_allowed(ProjectMode.SUGGEST_ONLY, report)
    assert allowed_modes(report) == [ProjectMode.AUDIT_ONLY, ProjectMode.SUGGEST_ONLY]


def test_no_report_allows_every_mode() -> None:
    for mode in ProjectMode:
        assert_mode_allowed(mode, None)
    assert allowed_modes(None) == list(ProjectMode)


def test_unknown_platform_is_rejected() -> None:
    with pytest.raises(UnknownPlatform, match="shopify"):
        report_for_platform("shopify")


def test_wordpress_report_is_honest() -> None:
    from app.connectors.capabilities import ModificationLevel, wordpress_capabilities

    report = wordpress_capabilities()
    assert report.platform == "wordpress"
    assert report.source_access is False
    assert report.theme_source is False
    assert report.theme_modification is ModificationLevel.LOW
    assert report.seo_modification is ModificationLevel.HIGH
    assert report.aeo_modification is ModificationLevel.HIGH
    assert report.geo_modification is ModificationLevel.HIGH
    assert report.automatic_rollback is ModificationLevel.HIGH
    assert report.snapshot is True
    assert report.pull_request is ModificationLevel.NONE
    assert report_for_platform("wordpress").platform == "wordpress"


def test_git_repository_allows_apply_locally() -> None:
    report = git_repository_capabilities()
    assert report.source_access is True
    assert report.allows_modification() is True
    assert_mode_allowed(ProjectMode.APPLY_LOCALLY, report)
    assert_mode_allowed(ProjectMode.COMMIT, report)
    assert ProjectMode.APPLY_LOCALLY in allowed_modes(report)
    assert ProjectMode.CREATE_PR not in allowed_modes(report)


def test_url_only_plus_git_unlocks_apply_locally() -> None:
    merged = project_capability_report(
        url_only_capabilities(), has_repository=True
    )
    assert merged is not None
    assert merged.platform == "git+url_only"
    assert merged.source_access is True
    assert merged.allows_modification() is True
    assert merged.snapshot is True
    assert_mode_allowed(ProjectMode.APPLY_LOCALLY, merged)
    assert ProjectMode.APPLY_LOCALLY in allowed_modes(merged)
    assert ProjectMode.CREATE_PR not in allowed_modes(merged)


def test_url_only_without_git_stays_read_only() -> None:
    merged = project_capability_report(
        url_only_capabilities(), has_repository=False
    )
    assert merged is not None
    assert merged.platform == "url_only"
    assert merged.allows_modification() is False
    with pytest.raises(ModeNotAllowed):
        assert_mode_allowed(ProjectMode.APPLY_LOCALLY, merged)


def test_git_only_project_report() -> None:
    merged = project_capability_report(None, has_repository=True)
    assert merged is not None
    assert merged.platform == "git"
    assert_mode_allowed(ProjectMode.APPLY_LOCALLY, merged)
