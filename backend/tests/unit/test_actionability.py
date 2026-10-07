from types import SimpleNamespace

from app.changes.actionability import effective_actionability

REPO = SimpleNamespace(id=1)


def _site(platform: str):
    return SimpleNamespace(platform=platform)


def test_stale_recommend_only_becomes_code_change_once_repository_attached() -> None:
    assert (
        effective_actionability("recommend_only", repository=REPO, website=_site("url_only"))
        == "code_change"
    )


def test_recommend_only_stays_without_repository_or_wordpress() -> None:
    assert (
        effective_actionability("recommend_only", repository=None, website=_site("url_only"))
        == "recommend_only"
    )
    assert effective_actionability("recommend_only", repository=None, website=None) == "recommend_only"


def test_recommend_only_becomes_platform_change_for_wordpress() -> None:
    assert (
        effective_actionability("recommend_only", repository=None, website=_site("wordpress"))
        == "code_or_platform_change"
    )


def test_code_actionable_values_are_kept() -> None:
    for value in ("code_change", "code_or_platform_change"):
        assert effective_actionability(value, repository=None, website=None) == value


def test_other_values_are_never_upgraded() -> None:
    assert effective_actionability("manual", repository=REPO, website=None) == "manual"
