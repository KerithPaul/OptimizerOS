"""Stale clone workspaces must be removable even with read-only git pack files.

Git marks `.git/objects/pack/*.idx` and `*.pack` read-only. A plain
`shutil.rmtree` on Windows raises `WinError 5` for those files even though
the process owns them, which previously made `clone_repository` fail
permanently on any repeat clone of the same repository/project pair.
"""

import os
import stat
from pathlib import Path

from app.intelligence.repository.clone import rmtree_readonly_safe


def test_rmtree_removes_readonly_files(tmp_path: Path) -> None:
    target = tmp_path / "stale-clone"
    (target / ".git" / "objects" / "pack").mkdir(parents=True)
    readonly_file = target / ".git" / "objects" / "pack" / "pack-abc.idx"
    readonly_file.write_bytes(b"pack data")
    os.chmod(readonly_file, stat.S_IREAD)

    rmtree_readonly_safe(target)

    assert not target.exists()


def test_rmtree_removes_paths_past_windows_max_path(tmp_path: Path) -> None:
    """A pnpm `.pnpm/<encoded-key>/node_modules/...` tree exceeds MAX_PATH
    (260 chars); without the `\\\\?\\` opt-out, Windows reports the file as
    missing (WinError 3, or WinError 206 while merely creating it) instead
    of the real problem. Build the tree via the same `\\\\?\\` prefix so
    setup itself doesn't hit that limit before the code under test does.
    """
    target = tmp_path / "stale-clone"
    deep = target / "node_modules" / ".pnpm"
    # Mirror a real pnpm dependency-key directory name: long enough on its
    # own that, combined with tmp_path, the full path clears 260 chars.
    deep = deep / ("@typescript-eslint+eslint-plugin@8.69.0_" * 3)
    deep = deep / "node_modules" / "@typescript-eslint" / "eslint-plugin" / "dist" / "configs"
    prefixed_deep = Path("\\\\?\\" + str(deep))
    prefixed_deep.mkdir(parents=True)
    long_file = prefixed_deep / "eslint-recommended-raw.d.ts"
    long_file.write_text("stub")
    assert len(str(deep)) > 260

    rmtree_readonly_safe(target)

    assert not target.exists()


def test_run_git_timeout_raises_clone_error_without_leaking_token(monkeypatch) -> None:
    import subprocess

    import pytest

    from app.intelligence.repository import clone

    secret = "ghp_SECRET"
    argv = ["git", "clone", f"https://x-access-token:{secret}@github.com/o/r"]

    def fake_run(*_args, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(clone.subprocess, "run", fake_run)

    with pytest.raises(clone.CloneError) as excinfo:
        clone.run_git(["clone", argv[2], "dest"], cwd=None)

    message = str(excinfo.value)
    assert secret not in message
    assert "timed out" in message


def test_git_error_output_is_redacted() -> None:
    from app.intelligence.repository.clone import _redact_credentials

    text = "fatal: unable to access 'https://x-access-token:ghp_SECRET@github.com/o/r/'"
    assert "ghp_SECRET" not in _redact_credentials(text)
