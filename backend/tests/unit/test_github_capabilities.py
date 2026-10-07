from app.connectors.capabilities import (
    ModificationLevel,
    github_token_capabilities,
    git_repository_capabilities,
    project_capability_report,
    url_only_capabilities,
)


def test_github_token_report_is_high_for_source_ast_git_code_rollback_pr() -> None:
    report = github_token_capabilities()
    assert report.source_access is True
    assert report.ast_access is True
    assert report.git_history is True
    assert report.code_modification is ModificationLevel.HIGH
    assert report.automatic_rollback is ModificationLevel.HIGH
    assert report.pull_request is ModificationLevel.HIGH


def test_git_without_token_does_not_claim_pr() -> None:
    report = git_repository_capabilities()
    assert report.pull_request is ModificationLevel.NONE
    assert report.code_modification is ModificationLevel.HIGH


def test_token_plus_git_raises_pr_to_high() -> None:
    merged = project_capability_report(
        None,
        has_repository=True,
        github_report=github_token_capabilities(),
    )
    assert merged is not None
    assert merged.pull_request is ModificationLevel.HIGH
    assert "git" in merged.platform
    assert "github" in merged.platform


def test_wordpress_hides_create_pr() -> None:
    from app.connectors.capabilities import allowed_modes, wordpress_capabilities
    from app.models.project import ProjectMode

    modes = allowed_modes(wordpress_capabilities())
    assert ProjectMode.COMMIT in modes
    assert ProjectMode.CREATE_PR not in modes


def test_url_only_plus_git_still_git_url_only() -> None:
    merged = project_capability_report(url_only_capabilities(), has_repository=True)
    assert merged is not None
    assert merged.platform == "git+url_only"
