"""Deterministic sandbox commands from the repository's architecture profile.

`Repository.architecture_profile` (step 2.B.4) is read-only ground truth
here — a package manager this module does not recognise means the
corresponding check is `skipped` with an honest reason, never guessed.

The same applies to a Node `package.json` missing the script a check would
run: `npm run test` on a project with no `test` script exits nonzero with
"Missing script: test", indistinguishable in a `SandboxResult` from a real
test failure the patch caused. `workspace`, when given, is read once to
tell those apart and skip with an honest reason instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_NODE_INSTALL: dict[str, list[str]] = {
    "npm": ["npm", "ci"],
    "pnpm": ["pnpm", "install", "--frozen-lockfile"],
    "yarn": ["yarn", "install", "--frozen-lockfile"],
    "bun": ["bun", "install"],
}
_NODE_RUN: dict[str, list[str]] = {
    "npm": ["npm", "run"],
    "pnpm": ["pnpm", "run"],
    "yarn": ["yarn", "run"],
    "bun": ["bun", "run"],
}
_PYTHON_INSTALL: dict[str, list[str]] = {
    "uv": ["uv", "sync"],
    "poetry": ["poetry", "install"],
    "pip": ["pip", "install", "-r", "requirements.txt"],
}


@dataclass(frozen=True)
class SandboxCommand:
    check_type: str
    command: list[str] | None
    skip_reason: str | None = None
    # True when the repo has no such step (missing Node script, Python has
    # no build). Distinct from an infrastructure skip: not_applicable must
    # not make a validation run PARTIAL.
    not_applicable: bool = False


def _is_python(profile: dict) -> bool:
    return str(profile.get("language") or "").lower() == "python"


def _read_package_json(workspace: Path | None) -> dict | None:
    if workspace is None:
        return None
    try:
        data = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _node_script_exists(workspace: Path | None, script: str) -> bool:
    """`True` when unknown: only a readable package.json can prove absence."""
    if workspace is None:
        return True
    data = _read_package_json(workspace)
    if data is None:
        return True
    return script in (data.get("scripts") or {})


def _is_next_project(workspace: Path | None, profile: dict) -> bool:
    blob = " ".join(
        str(profile.get(key) or "") for key in ("framework", "frontend", "build_system")
    ).lower()
    if "next" in blob:
        return True
    data = _read_package_json(workspace)
    if data is None:
        return False
    deps = {}
    deps.update(data.get("dependencies") or {})
    deps.update(data.get("devDependencies") or {})
    return "next" in deps


def install_command(profile: dict) -> SandboxCommand:
    manager = str(profile.get("package_manager") or "").lower()
    if _is_python(profile):
        command = _PYTHON_INSTALL.get(manager)
    else:
        command = _NODE_INSTALL.get(manager)
    if command is None:
        return SandboxCommand("install", None, f"unrecognized package_manager: {manager or '(none)'}")
    return SandboxCommand("install", command)


def build_command(profile: dict, workspace: Path | None = None) -> SandboxCommand:
    if _is_python(profile):
        return SandboxCommand(
            "build", None, "Python projects have no build step", not_applicable=True
        )
    if not _node_script_exists(workspace, "build"):
        return SandboxCommand(
            "build", None, 'no "build" script in package.json', not_applicable=True
        )
    manager = str(profile.get("package_manager") or "npm").lower()
    runner = _NODE_RUN.get(manager, _NODE_RUN["npm"])
    build = " ".join([*runner, "build"])
    # `workspace` is the repository's persistent checkout, reused across
    # every sandbox run rather than a fresh copy per run, so a `.next`
    # left behind by an earlier build (one that ran before
    # SANDBOX_RUN_AS_UID was set, or as a different uid) stays owned by
    # that uid forever. The sandbox always runs the build as a fixed,
    # non-root uid, so writing into a `.next` owned by someone else fails
    # with EACCES (e.g. Next.js's own `.next/trace`) even though the patch
    # under test is fine. Since a build's `.next` output is never read
    # after the check completes, always start from a clean one.
    return SandboxCommand("build", ["sh", "-c", f"rm -rf .next && {build}"])


def lint_command(profile: dict, workspace: Path | None = None) -> SandboxCommand:
    if _is_python(profile):
        return SandboxCommand("lint", ["ruff", "check", "."])
    if not _node_script_exists(workspace, "lint"):
        return SandboxCommand(
            "lint", None, 'no "lint" script in package.json', not_applicable=True
        )
    manager = str(profile.get("package_manager") or "npm").lower()
    runner = _NODE_RUN.get(manager, _NODE_RUN["npm"])
    return SandboxCommand("lint", [*runner, "lint"])


def unit_test_command(profile: dict, workspace: Path | None = None) -> SandboxCommand:
    if _is_python(profile):
        return SandboxCommand("unit_test", ["pytest", "-q"])
    if not _node_script_exists(workspace, "test"):
        return SandboxCommand(
            "unit_test", None, 'no "test" script in package.json', not_applicable=True
        )
    manager = str(profile.get("package_manager") or "npm").lower()
    runner = _NODE_RUN.get(manager, _NODE_RUN["npm"])
    return SandboxCommand("unit_test", [*runner, "test"])


def start_command(profile: dict, workspace: Path | None = None) -> SandboxCommand:
    """HTTP preview for post-change Playwright. Runs inside the sandbox.

    Missing start is a skip (cannot confirm SEO), not not_applicable —
    an SEO finding still needs a served app. Python has no default
    preview command; Next.js can fall back to `next start` after build.
    """

    if _is_python(profile):
        return SandboxCommand("start", None, "Python projects have no HTTP preview command")
    if _node_script_exists(workspace, "start"):
        manager = str(profile.get("package_manager") or "npm").lower()
        runner = _NODE_RUN.get(manager, _NODE_RUN["npm"])
        return SandboxCommand("start", [*runner, "start"])
    if _is_next_project(workspace, profile):
        return SandboxCommand(
            "start",
            [
                "sh",
                "-c",
                'exec node_modules/.bin/next start -H 0.0.0.0 -p "${PORT:-3000}"',
            ],
        )
    return SandboxCommand(
        "start",
        None,
        'no "start" script in package.json and no Next.js fallback',
    )
