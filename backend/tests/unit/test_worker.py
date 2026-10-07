"""Worker error handling (step 1.C.2 verify).

`jobs.error` / `agent_runs.error` are TEXT (MySQL: 65,535-byte cap). A
handler exception's str() has no size bound of its own -- e.g. a
SnapshotError wrapping one failure per file in a large copytree -- and
must never be allowed to turn "mark this job failed" into a second,
unhandled DB failure.
"""

from __future__ import annotations

from app.worker import _MAX_ERROR_CHARS, _truncate_error


def test_truncate_error_leaves_short_strings_alone() -> None:
    assert _truncate_error("boom") == "boom"


def test_truncate_error_truncates_oversized_strings() -> None:
    huge = "x" * (_MAX_ERROR_CHARS + 5000)
    result = _truncate_error(huge)
    assert len(result) < len(huge)
    assert result.startswith("x" * _MAX_ERROR_CHARS)
    assert str(len(huge)) in result
