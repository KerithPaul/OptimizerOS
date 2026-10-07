"""Semantic change units (step 8.2, `[SPEC AGENTS.md §37]`).

Splits a changed file's before/after content into the smallest practical
semantic units -- function/class/component boundaries for parseable code
(via `app.intelligence.repository.ast.parse_symbols`), heading-delimited
sections for Markdown, blank-line paragraphs for everything else -- so a
later rollback request ("undo only PricingTable", "undo only this
paragraph") can act on one unit without discarding the rest of the file.
A file type with no structural boundary, or a diff `parse_symbols` cannot
fully explain, always also gets a whole-file unit: the coarsest
granularity is never unavailable.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.intelligence.repository.ast import Symbol, parse_symbols

_CODE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".mdx"})
_KIND_FOR_SYMBOL = {"Function": "function", "Class": "class", "Component": "component"}
_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChangeUnit:
    kind: str
    symbol_name: str | None
    start_line: int | None
    end_line: int | None
    content_before: str
    content_after: str

    @property
    def hash_before(self) -> str:
        return hash_text(self.content_before)

    @property
    def hash_after(self) -> str:
        return hash_text(self.content_after)


@dataclass(frozen=True)
class _Block:
    start_line: int
    end_line: int
    text: str
    label: str | None = None


def diff_units(file_path: str, before: str, after: str) -> list[ChangeUnit]:
    """One `ChangeUnit` per smallest identifiable region that actually changed.

    Never empty for a real change: callers should not call this when
    `before == after` (an identity diff is handled upstream as a no-op).
    """

    if before == after:
        return []
    suffix = Path(file_path).suffix.lower()
    if suffix in _CODE_SUFFIXES:
        units = _diff_code_units(file_path, before, after)
    elif suffix in _MARKDOWN_SUFFIXES:
        units = _diff_blocks(_heading_blocks(before), _heading_blocks(after), kind="paragraph")
    else:
        units = _diff_blocks(_paragraph_blocks(before), _paragraph_blocks(after), kind="paragraph")
    return units if units else [_whole_file_unit(before, after)]


def _whole_file_unit(before: str, after: str) -> ChangeUnit:
    return ChangeUnit(
        kind="file", symbol_name=None, start_line=1, end_line=None,
        content_before=before, content_after=after,
    )


def _diff_code_units(file_path: str, before: str, after: str) -> list[ChangeUnit]:
    before_symbols = _named_symbols(before, file_path)
    after_symbols = _named_symbols(after, file_path)
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    units: list[ChangeUnit] = []
    covered_before: set[int] = set()
    covered_after: set[int] = set()

    for name in sorted(set(before_symbols) | set(after_symbols)):
        after_symbol = after_symbols.get(name)
        before_symbol = before_symbols.get(name)
        after_text = _slice(after_lines, after_symbol)
        before_text = _slice(before_lines, before_symbol)
        if after_text == before_text:
            continue
        if after_symbol is not None:
            covered_after.update(range(after_symbol.start_line, after_symbol.end_line + 1))
        if before_symbol is not None:
            covered_before.update(range(before_symbol.start_line, before_symbol.end_line + 1))
        reference = after_symbol or before_symbol
        units.append(
            ChangeUnit(
                kind=_KIND_FOR_SYMBOL.get(reference.kind, "other"),
                symbol_name=name,
                start_line=after_symbol.start_line if after_symbol else None,
                end_line=after_symbol.end_line if after_symbol else None,
                content_before=before_text,
                content_after=after_text,
            )
        )

    if _has_uncovered_change(before_lines, after_lines, covered_before, covered_after):
        units.append(_whole_file_unit(before, after))
    return units


def _named_symbols(source: str, file_path: str) -> dict[str, Symbol]:
    return {symbol.name: symbol for symbol in parse_symbols(source.encode("utf-8"), file_path)}


def _slice(lines: list[str], symbol: Symbol | None) -> str:
    if symbol is None or not symbol.start_line or not symbol.end_line:
        return ""
    start = max(1, symbol.start_line)
    end = min(len(lines), symbol.end_line)
    return "".join(lines[start - 1 : end])


def _has_uncovered_change(
    before_lines: list[str],
    after_lines: list[str],
    covered_before: set[int],
    covered_after: set[int],
) -> bool:
    leftover_before = "".join(
        line for index, line in enumerate(before_lines, start=1) if index not in covered_before
    )
    leftover_after = "".join(
        line for index, line in enumerate(after_lines, start=1) if index not in covered_after
    )
    return leftover_before != leftover_after


def _heading_blocks(text: str) -> list[_Block]:
    if not text:
        return []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return _paragraph_blocks(text)
    bounds = [match.start() for match in matches] + [len(text)]
    blocks: list[_Block] = []
    for index, match in enumerate(matches):
        section = text[match.start() : bounds[index + 1]]
        start_line = text[: match.start()].count("\n") + 1
        end_line = start_line + section.count("\n")
        heading = match.group(0).lstrip("#").strip()
        blocks.append(_Block(start_line=start_line, end_line=end_line, text=section, label=heading or None))
    return blocks


def _paragraph_blocks(text: str) -> list[_Block]:
    if not text.strip():
        return []
    blocks: list[_Block] = []
    current: list[str] = []
    start = 1
    line_no = 0
    for line in text.splitlines(keepends=True):
        line_no += 1
        if line.strip() == "":
            if current:
                blocks.append(_Block(start_line=start, end_line=line_no - 1, text="".join(current)))
                current = []
            start = line_no + 1
            continue
        if not current:
            start = line_no
        current.append(line)
    if current:
        blocks.append(_Block(start_line=start, end_line=line_no, text="".join(current)))
    return blocks


def _diff_blocks(before_blocks: list[_Block], after_blocks: list[_Block], *, kind: str) -> list[ChangeUnit]:
    before_texts = [block.text for block in before_blocks]
    after_texts = [block.text for block in after_blocks]
    matcher = difflib.SequenceMatcher(a=before_texts, b=after_texts, autojunk=False)

    units: list[ChangeUnit] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before_span = before_blocks[i1:i2]
        after_span = after_blocks[j1:j2]
        before_text = "".join(block.text for block in before_span)
        after_text = "".join(block.text for block in after_span)
        if before_text == after_text:
            continue
        label = next((b.label for b in after_span if b.label), None) or next(
            (b.label for b in before_span if b.label), None
        )
        units.append(
            ChangeUnit(
                kind=kind,
                symbol_name=label,
                start_line=after_span[0].start_line if after_span else None,
                end_line=after_span[-1].end_line if after_span else None,
                content_before=before_text,
                content_after=after_text,
            )
        )
    return units
