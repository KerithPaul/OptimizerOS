"""Where a heading-level skip lives in the source.

The crawl stores only the heading *levels* of the live page (`[1, 3, 3, 2, ...]`),
not which element produced them, so the Code Agent had to guess which tag to
change and could "fix" a skip by turning a `<p>` into an `<h4>`. This reads the
target file's own heading tags in source order and points at the first one that
jumps more than one level, so the patch can lower or raise that tag instead.

Source order is a hint, not proof: components rendered from other files are not
in the outline, and the Reviewer Agent plus the SEO re-check remain the verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.finding import Finding

HEADING_SKIP_RULE = "SEO-HEADING-SKIP-001"

# A literal `<hN` tag, or a component taking the tag name (`<EText as="h3">`).
_HEADING_TAG = re.compile(r"""<h([1-6])\b|\bas=["']h([1-6])["']""")
_SNIPPET_CHARS = 110


@dataclass(frozen=True)
class SourceHeading:
    line: int
    level: int
    code: str


def source_headings(content: str) -> list[SourceHeading]:
    found: list[SourceHeading] = []
    for number, text in enumerate(content.splitlines(), start=1):
        for match in _HEADING_TAG.finditer(text):
            level = int(match.group(1) or match.group(2))
            found.append(SourceHeading(number, level, text.strip()[:_SNIPPET_CHARS]))
    return found


def source_skips(headings: list[SourceHeading]) -> list[tuple[SourceHeading, SourceHeading]]:
    """`(previous, offender)` for every heading deeper than its predecessor + 1."""

    return [
        (previous, current)
        for previous, current in zip(headings, headings[1:])
        if current.level > previous.level + 1
    ]


def observed_levels(finding: Finding) -> list[int] | None:
    """Heading levels the crawler measured on the live page, in document order."""

    for row in finding.evidence or []:
        if not isinstance(row, dict) or row.get("confidence") != "direct":
            continue
        value = row.get("value")
        if isinstance(value, list) and value and all(isinstance(item, int) for item in value):
            return list(value)
    return None


def live_skip(levels: list[int]) -> tuple[int, int, int] | None:
    """`(position, previous_level, level)` of the first live skip; position is 1-based."""

    for index in range(1, len(levels)):
        if levels[index] > levels[index - 1] + 1:
            return index + 1, levels[index - 1], levels[index]
    return None


def skip_count(content: str) -> int:
    return len(source_skips(source_headings(content)))


def heading_skip_diagnosis(finding: Finding, files: dict[str, str]) -> dict | None:
    """Prompt data pointing the Code Agent at the heading tag to change, else None."""

    if finding.rule != HEADING_SKIP_RULE:
        return None
    diagnosis: dict = {
        "how_to_fix": (
            "Change the level of the existing heading tag named in `suspects` to one more "
            "than the heading before it (for example `as=\"h3\"` to `as=\"h2\"`, or `<h4` and "
            "`</h4>` to `<h3` and `</h3>`). Do not turn a paragraph, div, span, or any "
            "non-heading element into a heading, and do not add comments."
        ),
    }
    levels = observed_levels(finding)
    if levels is not None:
        diagnosis["live_heading_levels"] = levels
        skip = live_skip(levels)
        if skip is not None:
            position, before, after = skip
            diagnosis["live_first_skip"] = (
                f"heading #{position} on the live page is an H{after} directly after an H{before}"
            )
    suspects = []
    outline = {}
    for path, content in files.items():
        headings = source_headings(content)
        if not headings:
            continue
        outline[path] = [
            {"line": item.line, "level": item.level, "code": item.code} for item in headings
        ]
        for previous, current in source_skips(headings):
            suspects.append(
                {
                    "file": path,
                    "line": current.line,
                    "level": current.level,
                    "follows_level": previous.level,
                    "follows_line": previous.line,
                    "code": current.code,
                    "set_level_to": previous.level + 1,
                }
            )
    if outline:
        diagnosis["source_heading_outline"] = outline
    diagnosis["suspects"] = suspects
    return diagnosis
