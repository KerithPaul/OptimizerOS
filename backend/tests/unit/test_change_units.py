"""Semantic change units (step 8.2 verify, `[SPEC AGENTS.md §37]`)."""

from app.changes.units import diff_units


def test_python_function_level_change_is_isolated_to_that_function() -> None:
    before = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
    after = "def foo():\n    return 1\n\n\ndef bar():\n    return 3\n"

    units = diff_units("app/module.py", before, after)

    assert len(units) == 1
    unit = units[0]
    assert unit.kind == "function"
    assert unit.symbol_name == "bar"
    assert "return 2" in unit.content_before
    assert "return 3" in unit.content_after
    assert "foo" not in unit.content_before


def test_unrelated_function_is_not_touched_by_the_diff() -> None:
    before = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
    after = "def foo():\n    return 1\n\n\ndef bar():\n    return 3\n"

    units = diff_units("app/module.py", before, after)

    names = {u.symbol_name for u in units}
    assert "foo" not in names


def test_new_function_added_produces_a_unit_with_no_before_content() -> None:
    before = "def foo():\n    return 1\n"
    after = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"

    units = diff_units("app/module.py", before, after)

    bar_units = [u for u in units if u.symbol_name == "bar"]
    assert len(bar_units) == 1
    assert bar_units[0].content_before == ""
    assert "return 2" in bar_units[0].content_after


def test_markdown_heading_block_change_is_isolated_by_heading() -> None:
    before = "# Title\n\nHello world\n\n## Section\n\nOld text\n"
    after = "# Title\n\nHello world\n\n## Section\n\nNew text\n"

    units = diff_units("docs/readme.md", before, after)

    assert len(units) == 1
    assert units[0].kind == "paragraph"
    assert units[0].symbol_name == "Section"
    assert "Old text" in units[0].content_before
    assert "New text" in units[0].content_after
    assert "Hello world" not in units[0].content_before


def test_plain_text_paragraph_change_uses_blank_line_boundaries() -> None:
    before = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n"
    after = "First paragraph.\n\nSecond paragraph, edited.\n\nThird paragraph.\n"

    units = diff_units("content/page.html", before, after)

    assert len(units) == 1
    assert units[0].kind == "paragraph"
    assert "Second paragraph." in units[0].content_before
    assert "edited" in units[0].content_after
    assert "First paragraph" not in units[0].content_before
    assert "Third paragraph" not in units[0].content_before


def test_unparseable_code_falls_back_to_a_whole_file_unit() -> None:
    before = "def broken(:\n"
    after = "def broken(:\n    # still broken but different\n"

    units = diff_units("app/broken.py", before, after)

    assert len(units) == 1
    assert units[0].kind == "file"
    assert units[0].content_before == before
    assert units[0].content_after == after


def test_identity_diff_produces_no_units() -> None:
    text = "def foo():\n    return 1\n"
    assert diff_units("app/module.py", text, text) == []


def test_hash_before_and_after_are_always_present() -> None:
    before = "def foo():\n    return 1\n"
    after = "def foo():\n    return 2\n"

    units = diff_units("app/module.py", before, after)

    assert len(units) == 1
    assert units[0].hash_before
    assert units[0].hash_after
    assert units[0].hash_before != units[0].hash_after
