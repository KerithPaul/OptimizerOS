"""Semantic rollback (step 8.5, `[SPEC AGENTS.md §37]`).

Resolves one of the spec's example requests --

    Rollback the last SEO change.
    Rollback Change Set #42.
    Rollback only PricingTable.
    Undo only the paragraph ArchitectOS changed.
    Restore the previous version of this component.

-- to a concrete `ChangeSet` / `ChangeTransaction` / `ChangeItem` row,
combines the persisted record with the on-disk workspace to build a
`app.planners.rollback.RollbackPlan`, and -- once confirmed, or when the
plan is already high-confidence -- restores the smallest scope the
request actually asked for. `git revert HEAD` is never used: file-level
restore goes through the pre-change `Snapshot` (`app.changes.snapshot`);
finer-grained restore replaces just the recorded `ChangeItem` region.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.changes.snapshot import restore_snapshot
from app.changes.units import hash_text
from app.core.config import Settings, get_settings
from app.intelligence.repository.ast import parse_symbols
from app.intelligence.repository.clone import workspace_path
from app.models.change import (
    ChangeItem,
    ChangeItemKind,
    ChangeItemStatus,
    ChangePlatform,
    ChangeSet,
    ChangeSetStatus,
    ChangeTransaction,
    ChangeTransactionStatus,
    RollbackOperation,
    RollbackOperationStatus,
    RollbackTargetType,
    Snapshot,
)
from app.models.finding import Finding, FindingStatus
from app.models.knowledge import RuleCategory
from app.models.repository import Repository
from app.models.website import Website
from app.planners.rollback import RollbackPlan, TargetState, plan_rollback

_WP_METHOD = "wordpress_revision + architectos_snapshot"

_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "seo": (RuleCategory.TECHNICAL_SEO.value, RuleCategory.CONTENT_SEO.value),
    "technical_seo": (RuleCategory.TECHNICAL_SEO.value,),
    "content_seo": (RuleCategory.CONTENT_SEO.value,),
    "aeo": (RuleCategory.AEO.value,),
    "geo": (RuleCategory.GEO.value,),
}


class RollbackError(Exception):
    """The requested target could not be resolved, or is not eligible."""


@dataclass(frozen=True)
class RollbackTarget:
    target_type: RollbackTargetType
    change_set: ChangeSet | None = None
    change_transaction: ChangeTransaction | None = None
    change_item: ChangeItem | None = None


def resolve_target(
    db: Session,
    *,
    project_id: int,
    change_set_id: int | None = None,
    change_transaction_id: int | None = None,
    change_item_id: int | None = None,
    symbol_name: str | None = None,
    category: str | None = None,
) -> RollbackTarget:
    """Resolve exactly one selector to a concrete row.

    Ambiguity or a missing row is `RollbackError` -- never a best-effort
    guess at what the caller meant.
    """

    if change_item_id is not None:
        item = db.get(ChangeItem, change_item_id)
        if item is None or item.change_transaction.project_id != project_id:
            raise RollbackError(f"change item not found: {change_item_id}")
        return RollbackTarget(RollbackTargetType.CHANGE_ITEM, change_item=item)

    if change_transaction_id is not None:
        transaction = db.get(ChangeTransaction, change_transaction_id)
        if transaction is None or transaction.project_id != project_id:
            raise RollbackError(f"change transaction not found: {change_transaction_id}")
        return RollbackTarget(RollbackTargetType.CHANGE_TRANSACTION, change_transaction=transaction)

    if change_set_id is not None:
        change_set = db.get(ChangeSet, change_set_id)
        if change_set is None or change_set.project_id != project_id:
            raise RollbackError(f"change set not found: {change_set_id}")
        return RollbackTarget(RollbackTargetType.CHANGE_SET, change_set=change_set)

    if symbol_name:
        item = db.scalar(
            select(ChangeItem)
            .join(ChangeTransaction)
            .where(
                ChangeTransaction.project_id == project_id,
                ChangeItem.symbol_name == symbol_name,
                ChangeItem.status == ChangeItemStatus.APPLIED,
            )
            .order_by(ChangeItem.id.desc())
        )
        if item is None:
            raise RollbackError(f"no applied change touched a symbol/block named {symbol_name!r}")
        return RollbackTarget(RollbackTargetType.CHANGE_ITEM, change_item=item)

    if category:
        change_set = _latest_change_set_by_category(db, project_id, category)
        if change_set is None:
            raise RollbackError(f"no applied change set found for category {category!r}")
        return RollbackTarget(RollbackTargetType.CHANGE_SET, change_set=change_set)

    raise RollbackError("no rollback target selector given")


def _latest_change_set_by_category(db: Session, project_id: int, category: str) -> ChangeSet | None:
    categories = _CATEGORY_ALIASES.get(category.lower(), (category,))
    candidates = list(
        db.scalars(
            select(ChangeSet)
            .where(ChangeSet.project_id == project_id, ChangeSet.status == ChangeSetStatus.APPLIED)
            .order_by(ChangeSet.id.desc())
        )
    )
    for change_set in candidates:
        finding_ids = change_set.finding_ids_json or []
        if not finding_ids:
            continue
        findings = list(db.scalars(select(Finding).where(Finding.finding_id.in_(finding_ids))))
        if any(f.category.value in categories for f in findings):
            return change_set
    return None


def _repository_for(db: Session, project_id: int) -> Repository:
    repository = db.scalar(select(Repository).where(Repository.project_id == project_id))
    if repository is None:
        raise RollbackError("no repository attached to this project")
    return repository


def _applied_transactions(db: Session, change_set_id: int) -> list[ChangeTransaction]:
    return list(
        db.scalars(
            select(ChangeTransaction).where(
                ChangeTransaction.change_set_id == change_set_id,
                ChangeTransaction.status == ChangeTransactionStatus.APPLIED,
            )
        )
    )


def _resource_hash(workspace, resource: str) -> str | None:
    full = workspace / resource
    if not full.is_file():
        return None
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return hash_text(content)


def _current_symbol(workspace, resource: str, symbol_name: str):
    full = workspace / resource
    if not full.is_file() or not symbol_name:
        return None, None
    try:
        source = full.read_bytes()
        content = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    symbol = next((s for s in parse_symbols(source, resource) if s.name == symbol_name), None)
    if symbol is None or not symbol.start_line or not symbol.end_line:
        return None, None
    lines = content.splitlines(keepends=True)
    start = max(1, symbol.start_line)
    end = min(len(lines), symbol.end_line)
    return symbol, "".join(lines[start - 1 : end])


def _is_wordpress(db: Session, target: RollbackTarget) -> bool:
    if target.change_transaction is not None:
        return target.change_transaction.platform is ChangePlatform.WORDPRESS
    if target.change_item is not None:
        return target.change_item.change_transaction.platform is ChangePlatform.WORDPRESS
    if target.change_set is not None:
        rows = _applied_transactions(db, target.change_set.id)
        return bool(rows) and all(row.platform is ChangePlatform.WORDPRESS for row in rows)
    return False


def build_plan(
    db: Session, *, target: RollbackTarget, settings: Settings | None = None
) -> tuple[RollbackPlan, Repository | None, str]:
    """Compute the `RollbackPlan` for a resolved target. Read-only."""

    settings = settings or get_settings()

    if _is_wordpress(db, target):
        return _plan_for_wordpress(db, target, settings=settings)
    if target.target_type is RollbackTargetType.CHANGE_ITEM:
        return _plan_for_item(db, target.change_item, settings=settings)
    if target.target_type is RollbackTargetType.CHANGE_TRANSACTION:
        return _plan_for_transaction(db, target.change_transaction, settings=settings)
    return _plan_for_change_set(db, target.change_set, settings=settings)


def _plan_for_wordpress(
    db: Session, target: RollbackTarget, *, settings: Settings
) -> tuple[RollbackPlan, None, str]:
    snapshot_id = None
    label = "wordpress change"
    if target.change_set is not None:
        rows = _applied_transactions(db, target.change_set.id)
        snapshot_id = rows[0].snapshot_id if rows else None
        label = f"change set #{target.change_set.id} ({target.change_set.objective})"
    elif target.change_transaction is not None:
        snapshot_id = target.change_transaction.snapshot_id
        label = f"change transaction #{target.change_transaction.id}"
    elif target.change_item is not None:
        snapshot_id = target.change_item.change_transaction.snapshot_id
        label = f"change item #{target.change_item.id}"
    snapshot = db.get(Snapshot, snapshot_id) if snapshot_id else None
    state = TargetState(
        file_exists=snapshot is not None,
        symbol_located=snapshot is not None,
        current_hash_matches_after=True if snapshot is not None else None,
    )
    plan = plan_rollback(method=_WP_METHOD, state=state, settings=settings)
    return plan, None, label


def _plan_for_item(db: Session, item: ChangeItem, *, settings: Settings) -> tuple[RollbackPlan, Repository, str]:
    transaction = item.change_transaction
    repository = _repository_for(db, transaction.project_id)
    workspace = workspace_path(transaction.project_id, repository.id, settings)
    file_exists = (workspace / transaction.resource).is_file()

    if item.kind is ChangeItemKind.FILE:
        current_hash = _resource_hash(workspace, transaction.resource)
        state = TargetState(
            file_exists=file_exists,
            symbol_located=file_exists,
            current_hash_matches_after=(current_hash == item.hash_after) if file_exists else None,
        )
        method = "snapshot_restore"
    elif item.kind in (ChangeItemKind.FUNCTION, ChangeItemKind.CLASS_, ChangeItemKind.COMPONENT):
        symbol, current_text = _current_symbol(workspace, transaction.resource, item.symbol_name or "")
        located = symbol is not None
        state = TargetState(
            file_exists=file_exists,
            symbol_located=located,
            current_hash_matches_after=(hash_text(current_text) == item.hash_after) if located else None,
        )
        method = "ast_patch_reconstruction"
    else:
        content = None
        if file_exists:
            content = (workspace / transaction.resource).read_text(encoding="utf-8", errors="replace")
        located = bool(content) and bool(item.content_after) and item.content_after in content
        state = TargetState(
            file_exists=file_exists,
            symbol_located=located,
            current_hash_matches_after=True if located else (None if not file_exists else False),
        )
        method = "content_block_reconstruction"

    plan = plan_rollback(method=method, state=state, settings=settings)
    return plan, repository, f"change item #{item.id} ({item.symbol_name or item.kind.value})"


def _plan_for_transaction(
    db: Session, transaction: ChangeTransaction, *, settings: Settings
) -> tuple[RollbackPlan, Repository, str]:
    repository = _repository_for(db, transaction.project_id)
    workspace = workspace_path(transaction.project_id, repository.id, settings)
    current_hash = _resource_hash(workspace, transaction.resource)
    state = TargetState(
        file_exists=current_hash is not None,
        symbol_located=current_hash is not None,
        current_hash_matches_after=(current_hash == transaction.hash_after) if current_hash is not None else None,
    )
    plan = plan_rollback(method="snapshot_restore", state=state, settings=settings)
    return plan, repository, f"change transaction #{transaction.id} ({transaction.resource})"


def _plan_for_change_set(
    db: Session, change_set: ChangeSet, *, settings: Settings
) -> tuple[RollbackPlan, Repository, str]:
    repository = _repository_for(db, change_set.project_id)
    workspace = workspace_path(change_set.project_id, repository.id, settings)
    transactions = _applied_transactions(db, change_set.id)
    if not transactions:
        raise RollbackError(f"change set #{change_set.id} has no applied transactions left to roll back")

    plans: list[RollbackPlan] = []
    for transaction in transactions:
        current_hash = _resource_hash(workspace, transaction.resource)
        state = TargetState(
            file_exists=current_hash is not None,
            symbol_located=current_hash is not None,
            current_hash_matches_after=(current_hash == transaction.hash_after)
            if current_hash is not None
            else None,
        )
        plans.append(plan_rollback(method="snapshot_restore", state=state, settings=settings))

    confidence = min(p.confidence for p in plans)
    reasons = sorted({reason for p in plans for reason in p.reasons})
    plan = RollbackPlan(
        method="snapshot_restore",
        confidence=confidence,
        reasons=reasons,
        requires_confirmation=confidence < settings.rollback_confidence_threshold,
    )
    return plan, repository, f"change set #{change_set.id} ({change_set.objective})"


def execute_rollback(
    db: Session,
    *,
    project_id: int,
    target: RollbackTarget,
    plan: RollbackPlan,
    requested_target: str,
    confirmed: bool,
    settings: Settings | None = None,
) -> RollbackOperation:
    settings = settings or get_settings()

    operation = RollbackOperation(
        project_id=project_id,
        target_type=target.target_type,
        change_set_id=_change_set_id_of(target),
        change_transaction_id=_change_transaction_id_of(target),
        change_item_id=target.change_item.id if target.change_item else None,
        requested_target=requested_target,
        method=plan.method,
        confidence=plan.confidence,
        confidence_reasons_json=plan.reasons,
        requires_confirmation=plan.requires_confirmation,
        status=RollbackOperationStatus.PENDING_CONFIRMATION,
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)

    if plan.requires_confirmation and not confirmed:
        return operation

    try:
        if _is_wordpress(db, target):
            _rollback_wordpress(db, target, settings=settings)
        elif target.target_type is RollbackTargetType.CHANGE_ITEM:
            _rollback_item(db, target.change_item, settings=settings)
        elif target.target_type is RollbackTargetType.CHANGE_TRANSACTION:
            _rollback_transaction(db, target.change_transaction, settings=settings)
        else:
            _rollback_change_set(db, target.change_set, settings=settings)
    except RollbackError as exc:
        operation.status = RollbackOperationStatus.FAILED
        operation.result_detail = str(exc)
        operation.resolved_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(operation)
        return operation

    operation.status = RollbackOperationStatus.APPLIED
    operation.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(operation)
    return operation


def _change_set_id_of(target: RollbackTarget) -> int | None:
    if target.change_set is not None:
        return target.change_set.id
    if target.change_transaction is not None:
        return target.change_transaction.change_set_id
    if target.change_item is not None:
        return target.change_item.change_transaction.change_set_id
    return None


def _change_transaction_id_of(target: RollbackTarget) -> int | None:
    if target.change_transaction is not None:
        return target.change_transaction.id
    if target.change_item is not None:
        return target.change_item.change_transaction_id
    return None


def _rollback_wordpress(db: Session, target: RollbackTarget, *, settings: Settings) -> None:
    from app.connectors.wordpress.auth import WordPressAuthError, wordpress_connection
    from app.connectors.wordpress.connector import WordPressConnector
    from app.connectors.wordpress.rest import WordPressApiError
    from app.connectors.wordpress.snapshot import WordPressSnapshotError

    if target.change_set is not None:
        project_id = target.change_set.project_id
    elif target.change_transaction is not None:
        project_id = target.change_transaction.project_id
    else:
        project_id = target.change_item.change_transaction.project_id
    website = db.scalar(select(Website).where(Website.project_id == project_id))
    if website is None:
        raise RollbackError("no WordPress website attached to this project")
    connection = wordpress_connection(db, website.project_id)
    if connection is None:
        raise RollbackError("WordPress application password is not connected")
    try:
        connector = WordPressConnector.from_connection(
            connection, website.url, settings=settings
        )
        connector.authenticate()
        if target.change_item is not None:
            transaction = target.change_item.change_transaction
            snapshot = db.get(Snapshot, transaction.snapshot_id) if transaction.snapshot_id else None
            if snapshot is None:
                raise RollbackError("WordPress change has no ArchitectOS snapshot")
            connector.restore_snapshot_row(snapshot)
            target.change_item.status = ChangeItemStatus.ROLLED_BACK
            target.change_item.rolled_back_at = datetime.now(timezone.utc)
            transaction.status = ChangeTransactionStatus.ROLLED_BACK
            transaction.rolled_back_at = datetime.now(timezone.utc)
            db.commit()
            _maybe_mark_change_set_and_findings(db, transaction.change_set_id)
            return
        if target.change_transaction is not None:
            _rollback_wordpress_transaction(db, connector, target.change_transaction)
            return
        assert target.change_set is not None
        for transaction in _applied_transactions(db, target.change_set.id):
            _rollback_wordpress_transaction(db, connector, transaction)
    except WordPressAuthError as exc:
        raise RollbackError(f"WordPress authentication failure: {exc}") from exc
    except WordPressApiError as exc:
        raise RollbackError(f"WordPress API failure: {exc}") from exc
    except WordPressSnapshotError as exc:
        raise RollbackError(str(exc)) from exc


def _rollback_wordpress_transaction(db: Session, connector, transaction: ChangeTransaction) -> None:
    if transaction.platform is not ChangePlatform.WORDPRESS:
        raise RollbackError("refusing to git-rollback WordPress content")
    if transaction.status is not ChangeTransactionStatus.APPLIED:
        raise RollbackError(f"change transaction #{transaction.id} is already {transaction.status.value}")
    snapshot = db.get(Snapshot, transaction.snapshot_id) if transaction.snapshot_id else None
    if snapshot is None:
        raise RollbackError(f"change transaction #{transaction.id} has no snapshot to restore from")
    connector.restore_snapshot_row(snapshot)
    transaction.status = ChangeTransactionStatus.ROLLED_BACK
    transaction.rolled_back_at = datetime.now(timezone.utc)
    for item in transaction.items:
        if item.status is ChangeItemStatus.APPLIED:
            item.status = ChangeItemStatus.ROLLED_BACK
            item.rolled_back_at = datetime.now(timezone.utc)
    db.commit()
    _maybe_mark_change_set_and_findings(db, transaction.change_set_id)


def _rollback_transaction(db: Session, transaction: ChangeTransaction, *, settings: Settings) -> None:
    if transaction.platform is ChangePlatform.WORDPRESS:
        raise RollbackError("refusing to git-rollback WordPress content")
    if transaction.status is not ChangeTransactionStatus.APPLIED:
        raise RollbackError(f"change transaction #{transaction.id} is already {transaction.status.value}")
    snapshot = db.get(Snapshot, transaction.snapshot_id) if transaction.snapshot_id else None
    if snapshot is None:
        raise RollbackError(f"change transaction #{transaction.id} has no snapshot to restore from")

    restore_snapshot(snapshot, [transaction.resource], settings=settings)

    transaction.status = ChangeTransactionStatus.ROLLED_BACK
    transaction.rolled_back_at = datetime.now(timezone.utc)
    for item in transaction.items:
        if item.status is ChangeItemStatus.APPLIED:
            item.status = ChangeItemStatus.ROLLED_BACK
            item.rolled_back_at = datetime.now(timezone.utc)
    db.commit()
    _maybe_mark_change_set_and_findings(db, transaction.change_set_id)


def _rollback_change_set(db: Session, change_set: ChangeSet, *, settings: Settings) -> None:
    transactions = _applied_transactions(db, change_set.id)
    if not transactions:
        raise RollbackError(f"change set #{change_set.id} has no applied transactions left to roll back")
    for transaction in transactions:
        _rollback_transaction(db, transaction, settings=settings)


def _rollback_item(db: Session, item: ChangeItem, *, settings: Settings) -> None:
    if item.status is not ChangeItemStatus.APPLIED:
        raise RollbackError(f"change item #{item.id} is already {item.status.value}")

    transaction = item.change_transaction
    if item.kind is ChangeItemKind.FILE:
        _rollback_transaction(db, transaction, settings=settings)
        return

    repository = _repository_for(db, transaction.project_id)
    workspace = workspace_path(transaction.project_id, repository.id, settings)
    full = workspace / transaction.resource
    if not full.is_file():
        raise RollbackError(f"{transaction.resource} no longer exists in the workspace")

    if item.kind in (ChangeItemKind.FUNCTION, ChangeItemKind.CLASS_, ChangeItemKind.COMPONENT):
        symbol, _current_text = _current_symbol(workspace, transaction.resource, item.symbol_name or "")
        if symbol is None:
            raise RollbackError(
                f"symbol {item.symbol_name!r} could not be located in {transaction.resource}"
            )
        content = full.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        start = max(1, symbol.start_line)
        end = min(len(lines), symbol.end_line)
        new_lines = lines[: start - 1] + [item.content_before or ""] + lines[end:]
        full.write_text("".join(new_lines), encoding="utf-8")
    else:
        content = full.read_text(encoding="utf-8", errors="replace")
        if not item.content_after or item.content_after not in content:
            raise RollbackError(f"the changed block could not be located in {transaction.resource}")
        new_content = content.replace(item.content_after, item.content_before or "", 1)
        full.write_text(new_content, encoding="utf-8")

    item.status = ChangeItemStatus.ROLLED_BACK
    item.rolled_back_at = datetime.now(timezone.utc)
    db.commit()
    _maybe_mark_change_set_and_findings(db, transaction.change_set_id)


def _maybe_mark_change_set_and_findings(db: Session, change_set_id: int) -> None:
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None:
        return
    if _applied_transactions(db, change_set.id):
        if change_set.status is ChangeSetStatus.APPLIED:
            change_set.status = ChangeSetStatus.PARTIALLY_ROLLED_BACK
            db.commit()
        return

    change_set.status = ChangeSetStatus.ROLLED_BACK
    for finding_id in change_set.finding_ids_json or []:
        finding = db.scalar(
            select(Finding)
            .where(Finding.project_id == change_set.project_id, Finding.finding_id == finding_id)
            .order_by(Finding.id.desc())
        )
        if finding is not None:
            finding.status = FindingStatus.ROLLED_BACK
    db.commit()
