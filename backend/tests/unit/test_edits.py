"""Search/replace edits and windowed file views for the Code Agent."""

from __future__ import annotations

import pytest

from app.changes.edits import EditApplyError, TextEdit, apply_edits, render_file_view


def test_edit_replaces_one_exact_match() -> None:
    result = apply_edits("<head>\n</head>\n", [TextEdit(find="</head>", replace="<link>\n</head>")])
    assert result == "<head>\n<link>\n</head>\n"


def test_edits_apply_in_order_against_running_text() -> None:
    result = apply_edits(
        "a\nb\n",
        [TextEdit(find="a", replace="a1"), TextEdit(find="a1", replace="a2")],
    )
    assert result == "a2\nb\n"


def test_ambiguous_find_is_rejected() -> None:
    with pytest.raises(EditApplyError, match="matches 2 places"):
        apply_edits("x\nx\n", [TextEdit(find="x", replace="y")])


def test_missing_find_is_rejected() -> None:
    with pytest.raises(EditApplyError, match="not found"):
        apply_edits("hello\n", [TextEdit(find="absent", replace="y")])


def test_indentation_drift_is_tolerated_when_unique() -> None:
    content = "function f() {\n    return 1;\n}\n"
    result = apply_edits(content, [TextEdit(find="        return 1;", replace="    return 2;")])
    assert result == "function f() {\n    return 2;\n}\n"


def test_blank_find_is_invalid() -> None:
    with pytest.raises(ValueError):
        TextEdit(find="  ", replace="x")


def test_small_file_is_shown_whole() -> None:
    view = render_file_view("short file\n", ["short"], budget_chars=1000)
    assert view.complete is True
    assert view.text == "short file\n"


def _big_file() -> str:
    lines = [f"line {number} " + "x" * 40 + "\n" for number in range(1, 401)]
    lines[299] = "const CANONICAL = 'https://example.com/gone';\n"
    return "".join(lines)


def test_large_file_is_windowed_around_needle_and_fits_budget() -> None:
    content = _big_file()
    view = render_file_view(content, ["https://example.com/gone"], budget_chars=3000)

    assert view.complete is False
    assert "https://example.com/gone" in view.text
    assert "line 1 " in view.text  # head kept
    assert "omitted ..." in view.text
    assert len(view.text) < 3000 + 600  # budget plus the omission markers
    # Every shown line is verbatim, so a `find` copied from the view applies.
    shown = [line for line in view.text.splitlines() if not line.startswith("[...")]
    assert all(line in content for line in shown)


def test_large_file_without_needle_shows_head_and_tail() -> None:
    content = _big_file()
    view = render_file_view(content, ["nope"], budget_chars=3000)

    assert "line 1 " in view.text
    assert "line 400 " in view.text
    assert "omitted ..." in view.text


def test_single_enormous_line_falls_back_to_a_character_slice() -> None:
    content = "<head>" + "a" * 50_000 + "NEEDLE" + "b" * 50_000 + "</head>"
    view = render_file_view(content, ["NEEDLE"], budget_chars=2000)

    assert view.complete is False
    assert "NEEDLE" in view.text
    assert len(view.text) <= 2000
