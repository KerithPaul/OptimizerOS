"""Change Transaction hash guard (step 8.2 verify, `[SPEC AGENTS.md §29]`).

A transaction must never be persisted without both hashes -- verified
here by asserting the guard raises *before* touching the database (the
session argument is never used on the raising path).
"""

import pytest

from app.changes.transaction import TransactionError, record_transaction
from app.models.change import ValidationRunStatus


def test_transaction_cannot_be_persisted_without_after_content() -> None:
    with pytest.raises(TransactionError):
        record_transaction(
            None,  # type: ignore[arg-type]
            change_set_id=1,
            project_id=1,
            agent_run_id=None,
            snapshot_id=None,
            finding_id="SEO-001:abc",
            resource="app/page.tsx",
            content_before="old",
            content_after=None,
            reason="SEO-001",
            evidence=[],
            validation_status=ValidationRunStatus.PASSED,
        )


def test_transaction_cannot_be_persisted_without_before_content() -> None:
    with pytest.raises(TransactionError):
        record_transaction(
            None,  # type: ignore[arg-type]
            change_set_id=1,
            project_id=1,
            agent_run_id=None,
            snapshot_id=None,
            finding_id="SEO-001:abc",
            resource="app/page.tsx",
            content_before=None,
            content_after="new",
            reason="SEO-001",
            evidence=[],
            validation_status=ValidationRunStatus.PASSED,
        )
