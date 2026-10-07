"""Publish follow-up: COMMIT explains the missing PR; CREATE_PR opens one."""

from pathlib import Path
from types import SimpleNamespace

from app.connectors.github.publish import COMMIT_PR_SKIPPED, publish_change_set
from app.models.github import CommitStatus, GithubCommit
from app.models.project import ProjectMode


class _Db:
    def __init__(self, scalar_results: list) -> None:
        self.scalar_results = list(scalar_results)
        self.commits = 0

    def scalar(self, _stmt):
        return self.scalar_results.pop(0)

    def scalars(self, _stmt):
        return []

    def get(self, *_args, **_kwargs):
        return None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = 9

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = 9


def _pushed_commit() -> GithubCommit:
    commit = GithubCommit(
        project_id=404,
        repository_id=1,
        change_set_id=63,
        branch="architectos/63-heading",
        message="Reorder headings",
        files_json=["src/app/contact/page.tsx"],
        status=CommitStatus.PUSHED,
        sha="78570c0182fc",
    )
    commit.id = 15
    return commit


def _change_set():
    return SimpleNamespace(
        id=63,
        objective="Reorder the heading levels",
        finding_ids_json=["SEO-HEADING-SKIP-001:abc"],
        affected_resources_json=["src/app/contact/page.tsx"],
        evidence_json=[],
        risk="",
        validation_run_id=None,
        git_commit_ref=None,
        pull_request_ref=None,
    )


def test_commit_mode_republish_explains_why_no_pr() -> None:
    result = publish_change_set(
        _Db([_pushed_commit(), None]),  # type: ignore[arg-type]
        project=SimpleNamespace(id=404, mode=ProjectMode.COMMIT),  # type: ignore[arg-type]
        repository=SimpleNamespace(id=1, url="https://github.com/acme/site", default_branch="main"),  # type: ignore[arg-type]
        change_set=_change_set(),  # type: ignore[arg-type]
    )
    assert result.pull_request is None
    assert result.pull_request_skipped_reason == COMMIT_PR_SKIPPED
    assert result.as_json()["pull_request_skipped_reason"] == COMMIT_PR_SKIPPED


def test_create_pr_after_commit_opens_a_pr_without_pushing_again(monkeypatch) -> None:
    pushed: list[str] = []

    def _must_not_push(*_args, **_kwargs):
        pushed.append("push")
        raise AssertionError("commit_and_push must not run when the commit is already pushed")

    monkeypatch.setattr("app.connectors.github.publish.commit_and_push", _must_not_push)
    monkeypatch.setattr(
        "app.connectors.github.publish.workspace_path", lambda *_a, **_k: Path(".")
    )
    monkeypatch.setattr(
        "app.connectors.github.publish.require_pat", lambda *_a, **_k: (object(), "token")
    )
    monkeypatch.setattr(
        "app.connectors.github.publish.parse_github_repo", lambda _url: ("acme", "site")
    )

    def _create_pull_request(*_args, **kwargs):
        return {"number": 4, "html_url": "https://github.com/acme/site/pull/4", "head": kwargs["head"]}

    monkeypatch.setattr("app.connectors.github.publish.create_pull_request", _create_pull_request)
    monkeypatch.setattr(
        "app.connectors.github.publish.get_ci_status",
        lambda *_a, **_k: {"status": "unknown", "detail": None},
    )

    class _Rest:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    result = publish_change_set(
        _Db([_pushed_commit(), None]),  # type: ignore[arg-type]
        project=SimpleNamespace(id=404, mode=ProjectMode.CREATE_PR),  # type: ignore[arg-type]
        repository=SimpleNamespace(id=1, url="https://github.com/acme/site.git", default_branch="main"),  # type: ignore[arg-type]
        change_set=_change_set(),  # type: ignore[arg-type]
        rest_factory=lambda _token: _Rest(),
    )
    assert pushed == []
    assert result.pull_request is not None
    assert result.pull_request.number == 4
    assert result.pull_request.html_url == "https://github.com/acme/site/pull/4"
    assert result.pull_request_skipped_reason is None
    assert result.commit.sha == "78570c0182fc"
