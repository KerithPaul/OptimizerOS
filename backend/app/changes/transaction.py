"""Change Transaction (step 8.2, `[SPEC AGENTS.md §29]`).

The Change Transaction, not the Git commit, is ArchitectOS's central
mutation record: platform, resource, field, hash-before, hash-after,
reason (the rule_id), evidence, validation outcome, rollback method.
Beneath it, one `ChangeItem` per `app.changes.units.ChangeUnit` carries
its own hash-before/after so a rollback can act at file, function,
component, or content-block granularity without discarding the rest of
the file.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.changes.units import ChangeUnit, diff_units, hash_text
from app.models.change import (
    ChangeItem,
    ChangeItemKind,
    ChangeItemStatus,
    ChangePlatform,
    ChangeTransaction,
    ChangeTransactionStatus,
    ValidationRunStatus,
)

_UNIT_KIND: dict[str, ChangeItemKind] = {
    "file": ChangeItemKind.FILE,
    "function": ChangeItemKind.FUNCTION,
    "class": ChangeItemKind.CLASS_,
    "component": ChangeItemKind.COMPONENT,
    "paragraph": ChangeItemKind.PARAGRAPH,
}


class TransactionError(Exception):
    """A Change Transaction cannot be persisted without both hashes."""


def record_transaction(
    db: Session,
    *,
    change_set_id: int,
    project_id: int,
    agent_run_id: int | None,
    snapshot_id: int | None,
    finding_id: str,
    resource: str,
    content_before: str | None,
    content_after: str | None,
    reason: str,
    evidence: list,
    validation_status: ValidationRunStatus,
    platform: ChangePlatform = ChangePlatform.GIT,
    field: str = "file_content",
    rollback_method: str = "snapshot_restore + change_item_reconstruction",
) -> ChangeTransaction:
    """Persist one `ChangeTransaction` plus its `ChangeItem`s.

    `content_before`/`content_after` must both be real strings -- a
    transaction cannot be persisted without both hashes (step 8.2 verify).
    """

    if content_before is None or content_after is None:
        raise TransactionError(
            f"cannot record a Change Transaction for {resource!r} without both "
            "before and after content"
        )

    transaction = ChangeTransaction(
        change_set_id=change_set_id,
        project_id=project_id,
        agent_run_id=agent_run_id,
        snapshot_id=snapshot_id,
        finding_id=finding_id,
        platform=platform,
        resource=resource,
        field=field,
        hash_before=hash_text(content_before),
        hash_after=hash_text(content_after),
        reason=reason,
        evidence_json=evidence,
        validation_status=validation_status,
        rollback_method=rollback_method,
        status=ChangeTransactionStatus.APPLIED,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    for unit in diff_units(resource, content_before, content_after):
        db.add(_item_for_unit(transaction.id, unit))
    db.commit()
    return transaction


def _item_for_unit(change_transaction_id: int, unit: ChangeUnit) -> ChangeItem:
    kind = _UNIT_KIND.get(unit.kind, ChangeItemKind.OTHER)
    store_content = kind is not ChangeItemKind.FILE
    return ChangeItem(
        change_transaction_id=change_transaction_id,
        kind=kind,
        symbol_name=unit.symbol_name,
        start_line=unit.start_line,
        end_line=unit.end_line,
        hash_before=unit.hash_before,
        hash_after=unit.hash_after,
        content_before=unit.content_before if store_content else None,
        content_after=unit.content_after if store_content else None,
        status=ChangeItemStatus.APPLIED,
    )
