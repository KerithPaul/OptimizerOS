"""Logical commit + branch naming (step 9.3 / 8.4)."""

from pathlib import Path

import pytest

from app.connectors.github.git_ops import GitOpsError, branch_name, commit_and_push, slugify
from app.intelligence.repository.clone import run_git


def _git(cwd: Path, args: list[str]) -> str:
    return run_git(["-c", "user.name=test", "-c", "user.email=test@local", *args], cwd=cwd)


def _bare_and_workspace(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, ["init", "--bare"])
    workspace = tmp_path / "workspace"
    run_git(["clone", str(origin), str(workspace)], cwd=None)
    (workspace / "app").mkdir()
    (workspace / "app" / "meta.ts").write_text("export const title = 'a';\n", encoding="utf-8")
    (workspace / "app" / "schema.ts").write_text("export const schema = {};\n", encoding="utf-8")
    _git(workspace, ["add", "--", "app/meta.ts", "app/schema.ts"])
    _git(workspace, ["commit", "-m", "seed"])
    _git(workspace, ["push", "origin", "HEAD:refs/heads/main"])
    return origin, workspace


def test_branch_name_pattern() -> None:
    assert branch_name(42, "Improve product-page SEO!") == "architectos/42-improve-product-page-seo"
    assert slugify("") == "change"


def test_one_logical_commit_for_multiple_files(tmp_path: Path) -> None:
    origin, workspace = _bare_and_workspace(tmp_path)
    (workspace / "app" / "meta.ts").write_text("export const title = 'b';\n", encoding="utf-8")
    (workspace / "app" / "schema.ts").write_text("export const schema = {ok: true};\n", encoding="utf-8")
    (workspace / "unrelated.txt").write_text("leave me", encoding="utf-8")

    result = commit_and_push(
        workspace,
        files=["app/meta.ts", "app/schema.ts"],
        message="Improve product-page SEO\n\nChange Set #42",
        branch="architectos/42-improve-product-page-seo",
        remote_url=str(origin),
        token="ghp_should-never-appear",
    )

    assert result.sha
    assert result.branch == "architectos/42-improve-product-page-seo"
    assert set(result.files) == {"app/meta.ts", "app/schema.ts"}
    log = _git(workspace, ["log", "-1", "--name-only", "--pretty=format:"])
    assert "meta.ts" in log
    assert "schema.ts" in log
    assert "unrelated.txt" not in log
    count = _git(origin, ["rev-list", "--count", "architectos/42-improve-product-page-seo"]).strip()
    # seed + one logical commit
    assert count == "2"


def test_empty_file_list_is_refused(tmp_path: Path) -> None:
    origin, workspace = _bare_and_workspace(tmp_path)
    with pytest.raises(GitOpsError, match="empty file list"):
        commit_and_push(
            workspace,
            files=[],
            message="nope",
            branch="architectos/1-nope",
            remote_url=str(origin),
            token="secret",
        )


def test_git_error_redacts_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    origin, workspace = _bare_and_workspace(tmp_path)
    (workspace / "app" / "meta.ts").write_text("changed", encoding="utf-8")

    def boom(args, cwd):
        from app.intelligence.repository.clone import CloneError

        raise CloneError("https://x-access-token:ghp_secret@github.com/acme/shop.git denied")

    monkeypatch.setattr("app.connectors.github.git_ops.run_git", boom)
    with pytest.raises(GitOpsError) as exc:
        commit_and_push(
            workspace,
            files=["app/meta.ts"],
            message="x",
            branch="architectos/1-x",
            remote_url=str(origin),
            token="ghp_secret",
        )
    assert "ghp_secret" not in str(exc.value)
