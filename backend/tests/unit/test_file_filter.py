"""File filter include/skip counts on golden project A (step 2.B.3 verify)."""

import shutil
from pathlib import Path

from app.intelligence.repository.filter import filter_repository

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_A = _REPO_ROOT / "testdata" / "golden-projects" / "a-nextjs-ts-mysql"


def test_golden_a_file_count_is_plausible_and_reported() -> None:
    result = filter_repository(_GOLDEN_A)

    assert result.included_count == len(result.included_paths)
    assert result.included_count >= 6
    assert result.included_count < 100
    assert "package.json" in result.included_paths
    assert "app/page.tsx" in result.included_paths
    assert "lib/db.ts" in result.included_paths
    assert result.skipped_count == 0
    assert result.skipped_by_reason == {}


def test_skips_node_modules_git_build_output_binaries_and_lockfiles(
    tmp_path: Path,
) -> None:
    copy = tmp_path / "a"
    shutil.copytree(_GOLDEN_A, copy)
    (copy / "node_modules" / "next").mkdir(parents=True)
    (copy / "node_modules" / "next" / "index.js").write_text("module.exports = {}", encoding="utf-8")
    (copy / ".git").mkdir()
    (copy / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (copy / ".next").mkdir()
    (copy / ".next" / "trace").write_text("x", encoding="utf-8")
    (copy / "package-lock.json").write_text("{}", encoding="utf-8")
    (copy / "logo.png").write_bytes(b"\x89PNG")

    result = filter_repository(copy)

    assert result.included_count >= 6
    assert "package.json" in result.included_paths
    assert "node_modules/next/index.js" not in result.included_paths
    assert ".git/HEAD" not in result.included_paths
    assert ".next/trace" not in result.included_paths
    assert "package-lock.json" not in result.included_paths
    assert "logo.png" not in result.included_paths
    assert result.skipped_count >= 5
    assert result.skipped_by_reason["dir:node_modules"] >= 1
    assert result.skipped_by_reason["dir:.git"] >= 1
    assert result.skipped_by_reason["dir:.next"] >= 1
    assert result.skipped_by_reason["file:package-lock.json"] == 1
    assert result.skipped_by_reason["suffix:.png"] == 1
