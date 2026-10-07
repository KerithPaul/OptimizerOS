"""Search/replace edits and prompt-sized file views for the Code Agent.

Asking an LLM to re-emit a whole file costs the file's size twice (once in
the prompt, once in the answer) and silently loses anything it was never
shown. The Code Agent instead returns small `find`/`replace` edits; this
module applies them to the *full* on-disk content deterministically, so the
scope envelope and diff stats still run on the real before/after text and
the LLM is never trusted to reproduce the parts it did not change.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator

OMITTED_MARKER = "[... lines {start}-{end} omitted ...]"
_MAX_HITS = 6
_HEAD_SHARE = 0.25
_OVERSHOOT = 1.5
_PREVIEW_CHARS = 80


class EditApplyError(Exception):
    """An edit could not be applied unambiguously. Fed back to the LLM verbatim."""


class TextEdit(BaseModel):
    """Replace one exact occurrence of `find` with `replace`."""

    model_config = ConfigDict(extra="forbid")

    find: str
    replace: str

    @field_validator("find")
    @classmethod
    def _find_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("find must not be blank")
        return value


def _line_span_matches(lines: list[str], needle_lines: list[str]) -> list[int]:
    """Start indexes where `needle_lines` match `lines` ignoring indentation."""

    width = len(needle_lines)
    stripped = [line.strip() for line in lines]
    wanted = [line.strip() for line in needle_lines]
    return [
        start
        for start in range(len(stripped) - width + 1)
        if stripped[start : start + width] == wanted
    ]


def _apply_one(content: str, edit: TextEdit, index: int) -> str:
    count = content.count(edit.find)
    if count == 1:
        return content.replace(edit.find, edit.replace, 1)
    if count > 1:
        raise EditApplyError(
            f"edit {index}: `find` matches {count} places; include more "
            "surrounding lines so it matches exactly one"
        )

    # The model often drifts on indentation or trailing whitespace. Accept a
    # line-for-line match that differs only there, and only when it is unique.
    lines = content.splitlines(keepends=True)
    needle_lines = edit.find.strip("\r\n").splitlines()
    starts = _line_span_matches(lines, needle_lines) if needle_lines else []
    if len(starts) != 1:
        first_line = next((line.strip() for line in needle_lines if line.strip()), "")
        raise EditApplyError(
            f"edit {index}: `find` text was not found in the file (it starts with "
            f"{first_line[:_PREVIEW_CHARS]!r}); copy it verbatim from the file "
            "content shown, with surrounding lines"
        )
    start = starts[0]
    end = start + len(needle_lines)
    trailing_newline = lines[end - 1].endswith("\n")
    replacement = edit.replace.strip("\r\n")
    if trailing_newline and replacement:
        replacement += "\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


def apply_edits(content: str, edits: list[TextEdit]) -> str:
    """Apply `edits` in order; each must match exactly once in the running text."""

    for index, edit in enumerate(edits, start=1):
        content = _apply_one(content, edit, index)
    return content


@dataclass(frozen=True)
class FileView:
    text: str
    complete: bool


def _hit_lines(lines: list[str], needles: list[str]) -> list[int]:
    hits: list[int] = []
    for number, line in enumerate(lines):
        if any(needle in line for needle in needles):
            hits.append(number)
            if len(hits) >= _MAX_HITS:
                break
    return hits


def _grow(
    lines: list[str], start: int, end: int, budget: int, floor: int, ceil: int
) -> tuple[int, int]:
    """Grow [start, end) line-by-line, alternating sides, within `budget` chars."""

    used = sum(len(line) for line in lines[start:end])
    while True:
        grew = False
        if start > floor and used + len(lines[start - 1]) <= budget:
            start -= 1
            used += len(lines[start])
            grew = True
        if end < ceil and used + len(lines[end]) <= budget:
            used += len(lines[end])
            end += 1
            grew = True
        if not grew:
            return start, end


def render_file_view(content: str, needles: list[str], budget_chars: int) -> FileView:
    """The part of `content` worth showing the LLM within `budget_chars`.

    A file that fits is shown whole. Otherwise: the head (imports, `<head>`),
    plus windows around lines containing a needle (evidence locators, target
    symbols) — or, when nothing matches, the head and the tail. Omitted runs
    are marked so the model knows the text is not contiguous.
    """

    if len(content) <= budget_chars:
        return FileView(content, True)

    lines = content.splitlines(keepends=True)
    needles = [needle for needle in needles if needle]
    hits = _hit_lines(lines, needles)

    keep: list[tuple[int, int]] = []
    head_budget = int(budget_chars * (_HEAD_SHARE if hits else 0.6))
    head_end = 0
    used = 0
    while head_end < len(lines) and used + len(lines[head_end]) <= head_budget:
        used += len(lines[head_end])
        head_end += 1
    keep.append((0, head_end))

    remaining = budget_chars - used
    if hits:
        per_hit = max(remaining // len(hits), 200)
        for hit in hits:
            keep.append(_grow(lines, hit, hit + 1, per_hit, 0, len(lines)))
    else:
        tail_start = len(lines)
        tail_used = 0
        while tail_start > head_end and tail_used + len(lines[tail_start - 1]) <= remaining:
            tail_start -= 1
            tail_used += len(lines[tail_start])
        keep.append((tail_start, len(lines)))

    merged: list[list[int]] = []
    for start, end in sorted(keep):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            parts.append(OMITTED_MARKER.format(start=cursor + 1, end=start) + "\n")
        parts.append("".join(lines[start:end]))
        cursor = end
    if cursor < len(lines):
        parts.append(OMITTED_MARKER.format(start=cursor + 1, end=len(lines)) + "\n")
    text = "".join(parts)
    if len(text) > budget_chars * _OVERSHOOT:
        # A single enormous line (minified HTML/JS) defeats line windows:
        # slice characters around the first needle instead.
        first = min((content.find(n) for n in needles if n in content), default=0)
        begin = max(0, first - budget_chars // 2)
        return FileView(content[begin : begin + budget_chars], False)
    return FileView(text, False)
