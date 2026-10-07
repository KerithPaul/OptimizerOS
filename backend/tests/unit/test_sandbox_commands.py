"""Deterministic sandbox command selection (step 7.5, `[SPEC AGENTS.md §34]`).

`commands.py` must never emit a command guaranteed to fail for a reason
that has nothing to do with the patch under validation — an
unrecognized package manager, or (Node) a `package.json` missing the
script a check would run.
"""

from __future__ import annotations

import json

from app.changes.commands import (
    build_command,
    install_command,
    lint_command,
    start_command,
    unit_test_command,
)


def _node_profile() -> dict:
    return {"language": "TypeScript", "package_manager": "pnpm"}


def _write_package_json(tmp_path, scripts: dict) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")


def test_unit_test_command_runs_when_script_present(tmp_path) -> None:
    _write_package_json(tmp_path, {"build": "next build", "test": "jest"})
    command = unit_test_command(_node_profile(), tmp_path)
    assert command.command == ["pnpm", "run", "test"]
    assert command.skip_reason is None


def test_unit_test_command_skips_when_script_missing(tmp_path) -> None:
    """Without this, `pnpm run test` exits nonzero with "Missing script:
    test" — indistinguishable in a SandboxResult from a real test failure
    the patch caused."""
    _write_package_json(tmp_path, {"build": "next build", "lint": "eslint"})
    command = unit_test_command(_node_profile(), tmp_path)
    assert command.command is None
    assert command.skip_reason == 'no "test" script in package.json'
    assert command.not_applicable is True


def test_build_command_skips_when_script_missing(tmp_path) -> None:
    _write_package_json(tmp_path, {"lint": "eslint"})
    command = build_command(_node_profile(), tmp_path)
    assert command.command is None
    assert command.skip_reason == 'no "build" script in package.json'
    assert command.not_applicable is True


def test_lint_command_skips_when_script_missing(tmp_path) -> None:
    _write_package_json(tmp_path, {"build": "next build"})
    command = lint_command(_node_profile(), tmp_path)
    assert command.command is None
    assert command.skip_reason == 'no "lint" script in package.json'
    assert command.not_applicable is True


def test_node_commands_run_when_workspace_not_given() -> None:
    """No workspace to check against -> keep the pre-existing behavior
    rather than guessing."""
    assert unit_test_command(_node_profile()).command == ["pnpm", "run", "test"]
    assert build_command(_node_profile()).command == ["sh", "-c", "rm -rf .next && pnpm run build"]
    assert lint_command(_node_profile()).command == ["pnpm", "run", "lint"]


def test_build_command_cleans_next_before_building() -> None:
    """The workspace is a persistent checkout reused across sandbox runs,
    not a fresh copy per run: a `.next` left behind by an earlier build
    (e.g. one that ran as a different uid, before SANDBOX_RUN_AS_UID was
    set) stays owned by that uid and makes every later build fail with
    EACCES writing into it. Always start from a clean `.next`."""
    command = build_command(_node_profile())
    assert command.command == ["sh", "-c", "rm -rf .next && pnpm run build"]


def test_node_commands_run_when_package_json_missing(tmp_path) -> None:
    """No package.json to check against -> keep the pre-existing behavior
    rather than guessing."""
    assert unit_test_command(_node_profile(), tmp_path).command == ["pnpm", "run", "test"]


def test_python_commands_are_unaffected_by_workspace(tmp_path) -> None:
    profile = {"language": "python", "package_manager": "uv"}
    assert unit_test_command(profile, tmp_path).command == ["pytest", "-q"]
    assert build_command(profile, tmp_path).skip_reason == "Python projects have no build step"


def test_install_command_unaffected_by_this_change() -> None:
    command = install_command(_node_profile())
    assert command.command == ["pnpm", "install", "--frozen-lockfile"]


def test_start_command_uses_start_script_when_present(tmp_path) -> None:
    _write_package_json(tmp_path, {"start": "next start", "build": "next build"})
    command = start_command(_node_profile(), tmp_path)
    assert command.command == ["pnpm", "run", "start"]
    assert command.not_applicable is False


def test_start_command_falls_back_to_next_binary(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "next build"}, "dependencies": {"next": "15.0.0"}}),
        encoding="utf-8",
    )
    command = start_command(_node_profile(), tmp_path)
    assert command.command is not None
    assert "next start" in " ".join(command.command)


def test_start_command_skips_without_start_or_next(tmp_path) -> None:
    _write_package_json(tmp_path, {"build": "vite build"})
    command = start_command(_node_profile(), tmp_path)
    assert command.command is None
    assert command.not_applicable is False


def test_python_start_is_skipped() -> None:
    command = start_command({"language": "python", "package_manager": "uv"})
    assert command.command is None


def test_python_build_is_not_applicable() -> None:
    command = build_command({"language": "python", "package_manager": "uv"})
    assert command.not_applicable is True
