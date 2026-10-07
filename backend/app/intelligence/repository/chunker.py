"""Semantic chunker (step 2.D.1).

Primary boundaries are functions, classes, React components, routes,
modules, documentation sections, configuration blocks, SEO and schema
implementations. Bounded token windows are a fallback only when a file
has no structural boundary.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.intelligence.repository.ast import ExtractResult, Symbol

_KIND_TO_CHUNK = {
    "Function": "function",
    "Class": "class",
    "Component": "component",
    "SEOImplementation": "seo_implementation",
    "Schema": "schema_implementation",
    "API": "function",
}

_CONFIG_FILES = frozenset(
    {
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        "next.config.ts",
        "next.config.js",
        "next.config.mjs",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "composer.json",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "vercel.json",
    }
)

_FALLBACK_LINE_WINDOW = 80
_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)


@dataclass
class CodeChunk:
    chunk_type: str
    file_path: str
    symbol: str | None
    language: str | None
    start_line: int
    end_line: int
    text: str
    content_hash: str


def chunk_repository(root: Path, extracted: ExtractResult) -> list[CodeChunk]:
    """Build semantic chunks from extracted symbols and leftover file regions."""
    root = root.resolve()
    chunks: list[CodeChunk] = []
    covered: dict[str, set[int]] = {}

    structural = [
        s
        for s in extracted.symbols
        if s.kind in _KIND_TO_CHUNK and s.file_path and s.start_line and s.end_line
    ]
    for symbol in structural:
        chunk = _chunk_from_symbol(root, symbol)
        if chunk is None:
            continue
        chunks.append(chunk)
        covered.setdefault(symbol.file_path, set()).update(
            range(chunk.start_line, chunk.end_line + 1)
        )

    seen_files = {s.file_path for s in extracted.symbols if s.kind == "File" and s.file_path}
    for rel in sorted(seen_files):
        path = root / rel
        if not path.is_file():
            continue
        if Path(rel).name in _CONFIG_FILES or rel in _CONFIG_FILES:
            if rel not in covered:
                chunk = _whole_file_chunk(path, rel, "configuration")
                if chunk:
                    chunks.append(chunk)
                    covered[rel] = set(range(chunk.start_line, chunk.end_line + 1))
            continue
        if Path(rel).suffix.lower() in {".md", ".mdx", ".rst"}:
            for chunk in _documentation_chunks(path, rel):
                chunks.append(chunk)
                covered.setdefault(rel, set()).update(range(chunk.start_line, chunk.end_line + 1))
            continue
        leftovers = _module_leftovers(path, rel, covered.get(rel, set()))
        chunks.extend(leftovers)

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in covered or rel in seen_files:
            continue
        if Path(rel).name in _CONFIG_FILES:
            chunk = _whole_file_chunk(path, rel, "configuration")
            if chunk:
                chunks.append(chunk)
        elif Path(rel).suffix.lower() in {".md", ".mdx"}:
            chunks.extend(_documentation_chunks(path, rel))

    chunks.sort(key=lambda c: (c.file_path, c.start_line, c.chunk_type))
    return chunks


def _chunk_from_symbol(root: Path, symbol: Symbol) -> CodeChunk | None:
    assert symbol.file_path and symbol.start_line and symbol.end_line
    path = root / symbol.file_path
    lines = _read_lines(path)
    if not lines:
        return None
    start = max(1, symbol.start_line)
    end = min(len(lines), symbol.end_line)
    text = "".join(lines[start - 1 : end])
    if not text.strip():
        return None
    return CodeChunk(
        chunk_type=_KIND_TO_CHUNK[symbol.kind],
        file_path=symbol.file_path,
        symbol=symbol.name,
        language=symbol.language,
        start_line=start,
        end_line=end,
        text=text,
        content_hash=_hash_text(text),
    )


def _whole_file_chunk(path: Path, rel: str, chunk_type: str) -> CodeChunk | None:
    lines = _read_lines(path)
    if not lines:
        return None
    text = "".join(lines)
    return CodeChunk(
        chunk_type=chunk_type,
        file_path=rel,
        symbol=None,
        language=_language_for(rel),
        start_line=1,
        end_line=len(lines),
        text=text,
        content_hash=_hash_text(text),
    )


def _documentation_chunks(path: Path, rel: str) -> list[CodeChunk]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        lines = text.splitlines(keepends=True) or [text]
        chunk = CodeChunk(
            chunk_type="documentation",
            file_path=rel,
            symbol=None,
            language=None,
            start_line=1,
            end_line=len(lines),
            text=text,
            content_hash=_hash_text(text),
        )
        return [chunk]

    starts = [m.start() for m in matches]
    starts.append(len(text))
    chunks: list[CodeChunk] = []
    for i, match in enumerate(matches):
        section = text[match.start() : starts[i + 1]]
        start_line = text[: match.start()].count("\n") + 1
        end_line = start_line + section.count("\n")
        if section.endswith("\n"):
            end_line = start_line + section.count("\n")
        heading = match.group(0).lstrip("#").strip()
        chunks.append(
            CodeChunk(
                chunk_type="documentation",
                file_path=rel,
                symbol=heading or None,
                language=None,
                start_line=start_line,
                end_line=max(start_line, end_line),
                text=section,
                content_hash=_hash_text(section),
            )
        )
    return chunks


def _module_leftovers(path: Path, rel: str, covered: set[int]) -> list[CodeChunk]:
    lines = _read_lines(path)
    if not lines:
        return []
    leftover_idx = [i for i in range(1, len(lines) + 1) if i not in covered]
    if not leftover_idx:
        return []
    runs: list[tuple[int, int]] = []
    run_start = leftover_idx[0]
    prev = leftover_idx[0]
    for idx in leftover_idx[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        runs.append((run_start, prev))
        run_start = idx
        prev = idx
    runs.append((run_start, prev))

    chunks: list[CodeChunk] = []
    for start, end in runs:
        span_lines = lines[start - 1 : end]
        if not "".join(span_lines).strip():
            continue
        if end - start + 1 > _FALLBACK_LINE_WINDOW:
            chunks.extend(_window_chunks(rel, lines, start, end))
        else:
            text = "".join(span_lines)
            chunks.append(
                CodeChunk(
                    chunk_type="module",
                    file_path=rel,
                    symbol=None,
                    language=_language_for(rel),
                    start_line=start,
                    end_line=end,
                    text=text,
                    content_hash=_hash_text(text),
                )
            )
    return chunks


def _window_chunks(rel: str, lines: list[str], start: int, end: int) -> list[CodeChunk]:
    chunks: list[CodeChunk] = []
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + _FALLBACK_LINE_WINDOW - 1)
        text = "".join(lines[cursor - 1 : window_end])
        if text.strip():
            chunks.append(
                CodeChunk(
                    chunk_type="module",
                    file_path=rel,
                    symbol=None,
                    language=_language_for(rel),
                    start_line=cursor,
                    end_line=window_end,
                    text=text,
                    content_hash=_hash_text(text),
                )
            )
        cursor = window_end + 1
    return chunks


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    if not text.endswith("\n") and lines:
        return lines
    return lines


def _language_for(rel: str) -> str | None:
    suffix = Path(rel).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "javascript",
        ".json": "json",
        ".css": "css",
        ".html": "html",
    }.get(suffix)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
