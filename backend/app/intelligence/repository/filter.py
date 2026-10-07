"""Deterministic repository file filter (step 2.B.3).

Skips `node_modules`, `.git`, build output, binaries, and lockfiles.
Counts of included and skipped files are returned so a suspiciously empty
index is visible. Configurable via `FilterConfig`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".next",
        ".nuxt",
        ".output",
        ".turbo",
        ".cache",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "out",
        "coverage",
        "target",
        "vendor",
        "__pycache__",
    }
)

_DEFAULT_SKIP_FILES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "uv.lock",
        "Cargo.lock",
        "composer.lock",
        "poetry.lock",
        "go.sum",
        "Pipfile.lock",
    }
)

_DEFAULT_SKIP_SUFFIXES = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".o",
        ".a",
        ".pyc",
        ".pyo",
        ".wasm",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".svg",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".7z",
        ".mp4",
        ".mp3",
        ".webm",
        ".sqlite",
        ".db",
        ".min.js",
        ".min.css",
        ".map",
        ".lock",
    }
)


@dataclass(frozen=True)
class FilterConfig:
    skip_dirs: frozenset[str] = _DEFAULT_SKIP_DIRS
    skip_files: frozenset[str] = _DEFAULT_SKIP_FILES
    skip_suffixes: frozenset[str] = _DEFAULT_SKIP_SUFFIXES


@dataclass
class FilterResult:
    included_paths: list[str]
    included_count: int
    skipped_count: int
    skipped_by_reason: dict[str, int] = field(default_factory=dict)


def filter_repository(root: Path, config: FilterConfig | None = None) -> FilterResult:
    """Walk `root` and return included relative POSIX paths plus skip counts."""
    config = config or FilterConfig()
    root = root.resolve()
    included: list[str] = []
    skipped_by_reason: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            if name in config.skip_dirs:
                nested = _count_files_under(current / name)
                if nested:
                    reason = f"dir:{name}"
                    skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + nested
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            relative = (current / name).relative_to(root).as_posix()
            reason = _skip_file_reason(name, config)
            if reason is not None:
                skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
                continue
            included.append(relative)

    included.sort()
    skipped_count = sum(skipped_by_reason.values())
    return FilterResult(
        included_paths=included,
        included_count=len(included),
        skipped_count=skipped_count,
        skipped_by_reason=skipped_by_reason,
    )


def _count_files_under(path: Path) -> int:
    total = 0
    for _, _, filenames in os.walk(path, followlinks=False):
        total += len(filenames)
    return total


def _skip_file_reason(name: str, config: FilterConfig) -> str | None:
    if name in config.skip_files:
        return f"file:{name}"
    lowered = name.lower()
    for suffix in config.skip_suffixes:
        if lowered.endswith(suffix):
            return f"suffix:{suffix}"
    return None
