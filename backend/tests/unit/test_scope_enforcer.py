"""Scope enforcer (step 7.4 verify, `[SPEC AGENTS.md §33]`).

The canonical example from the plan: a Change Plan scoped to
`app/products/**` must reject a diff that touches `auth/**` or
`database/**`.
"""

from __future__ import annotations

from app.changes.scope import (
    ScopeEnvelope,
    check_scope,
    diff_stats,
    envelope_for_plan,
    is_path_safe,
)
from app.core.config import Settings
from app.planners.change import ChangePlan


def _settings(**overrides: object) -> Settings:
    values = {
        "APP_SECRET_KEY": "unit-test-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "cU5b7d2m9zQwErTyUiOpAsDfGhJkLzXcVbNmQwErTy8=",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _plan(target_files: list[str]) -> ChangePlan:
    return ChangePlan(
        finding_id="SEO-TEST-001:abc",
        target_files=target_files,
        target_symbols=[],
        reuse_notes="reuse existing metadata helper",
        expected_diff_summary="add canonical tag",
        required_tests=[],
        required_validation=[],
    )


def test_canonical_example_rejects_auth_and_database() -> None:
    envelope = envelope_for_plan(_settings(), _plan(["app/products/page.tsx"]))
    diffs = [
        diff_stats("auth/login.ts", "old", "new"),
        diff_stats("database/schema.sql", "old", "new"),
    ]
    violation = check_scope(envelope, ["auth/login.ts", "database/schema.sql"], diffs)
    assert violation is not None
    assert violation.reason == "forbidden_directory"


def test_in_scope_change_passes() -> None:
    envelope = envelope_for_plan(_settings(), _plan(["app/products/page.tsx"]))
    diffs = [diff_stats("app/products/page.tsx", "old content", "new content")]
    violation = check_scope(envelope, ["app/products/page.tsx"], diffs)
    assert violation is None


def test_file_outside_planned_directory_is_rejected() -> None:
    envelope = envelope_for_plan(_settings(), _plan(["app/products/page.tsx"]))
    diffs = [diff_stats("app/checkout/page.tsx", "old", "new")]
    violation = check_scope(envelope, ["app/checkout/page.tsx"], diffs)
    assert violation is not None
    assert violation.reason == "outside_planned_scope"


def test_too_many_files_rejected() -> None:
    envelope = ScopeEnvelope(
        allowed_directories=("app/products",),
        forbidden_directories=(),
        max_files_changed=1,
        max_lines_changed=1000,
        max_diff_bytes=100_000,
    )
    diffs = [
        diff_stats("app/products/a.tsx", "x", "y"),
        diff_stats("app/products/b.tsx", "x", "y"),
    ]
    violation = check_scope(envelope, ["app/products/a.tsx", "app/products/b.tsx"], diffs)
    assert violation is not None
    assert violation.reason == "too_many_files"


def test_too_many_lines_rejected() -> None:
    envelope = ScopeEnvelope(
        allowed_directories=("app/products",),
        forbidden_directories=(),
        max_files_changed=10,
        max_lines_changed=1,
        max_diff_bytes=100_000,
    )
    before = "\n".join(str(i) for i in range(20))
    after = "\n".join(str(i) for i in range(40))
    diffs = [diff_stats("app/products/a.tsx", before, after)]
    violation = check_scope(envelope, ["app/products/a.tsx"], diffs)
    assert violation is not None
    assert violation.reason == "too_many_lines"


def test_diff_too_large_rejected() -> None:
    envelope = ScopeEnvelope(
        allowed_directories=("app/products",),
        forbidden_directories=(),
        max_files_changed=10,
        max_lines_changed=10_000,
        max_diff_bytes=10,
    )
    diffs = [diff_stats("app/products/a.tsx", "x" * 100, "y" * 200)]
    violation = check_scope(envelope, ["app/products/a.tsx"], diffs)
    assert violation is not None
    assert violation.reason == "diff_too_large"


def test_no_files_rejected() -> None:
    envelope = ScopeEnvelope(
        allowed_directories=("app/products",),
        forbidden_directories=(),
        max_files_changed=10,
        max_lines_changed=10_000,
        max_diff_bytes=100_000,
    )
    assert check_scope(envelope, [], []).reason == "no_files"


def test_path_traversal_is_never_safe() -> None:
    assert is_path_safe("app/products/page.tsx") is True
    assert is_path_safe("../../etc/passwd") is False
    assert is_path_safe("/etc/passwd") is False
    assert is_path_safe("app/../../secret") is False
    assert is_path_safe("C:/Windows/system32") is False


def test_path_traversal_rejected_even_when_it_matches_the_plan() -> None:
    """The envelope's own allowed-directory derivation must not launder a
    traversal path just because the (also LLM-authored) Change Plan named
    the same traversal string as its target file.
    """

    envelope = envelope_for_plan(_settings(), _plan(["../../etc/passwd"]))
    assert envelope.allowed_directories == ()
    diffs = [diff_stats("../../etc/passwd", "old", "new")]
    violation = check_scope(envelope, ["../../etc/passwd"], diffs)
    assert violation is not None
    assert violation.reason == "path_traversal"


def test_diff_stats_are_deterministic_not_llm_reported() -> None:
    diff = diff_stats("a.py", "line1\nline2\n", "line1\nline2\nline3\n")
    assert diff.lines_added == 1
    assert diff.lines_removed == 0
    assert "+line3" in diff.unified_diff
