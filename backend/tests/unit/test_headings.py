"""Source-order heading outline used to point the Code Agent at a skip."""

from __future__ import annotations

from app.changes.headings import live_skip, skip_count, source_headings, source_skips


def test_source_headings_reads_tags_and_as_props_with_line_numbers() -> None:
    items = source_headings('<h1>A</h1>\n<EText as="h3" path="t" />\n<p>x</p>\n')
    assert [(item.line, item.level) for item in items] == [(1, 1), (2, 3)]


def test_source_skips_reports_previous_and_offender() -> None:
    (previous, offender), = source_skips(source_headings("<h2>a</h2>\n<h4>b</h4>\n<h3>c</h3>\n"))
    assert (previous.level, offender.level, offender.line) == (2, 4, 2)


def test_a_drop_in_level_is_not_a_skip() -> None:
    assert skip_count("<h1>a</h1>\n<h2>b</h2>\n<h3>c</h3>\n<h2>d</h2>\n") == 0


def test_live_skip_is_one_based_and_consecutive() -> None:
    assert live_skip([1, 3, 2]) == (2, 1, 3)
    assert live_skip([1, 2, 3, 2, 3]) is None
