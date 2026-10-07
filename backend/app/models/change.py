"""`snapshots`, `validation_runs`, `validation_results` — Migration 7 (step 7.7).
`change_sets`, `change_transactions`, `change_items`, `rollback_operations`
— Migration 8 (step 8.1, `[SPEC AGENTS.md §29-§30, §37-§38]`).

The Change Transaction, not the Git commit, is the central mutation
record `[SPEC]`: platform, resource, field, hash-before, hash-after,
reason (the rule_id), evidence, validation outcome, rollback method.
`ChangeItem` is the finer-grained semantic unit beneath one transaction
(function, class, component, or content-block) so a rollback request can
act on "only PricingTable" or "only this paragraph" without discarding
the rest of the file — see `app.changes.units`. Both carry their own
hash-before/after (step 8.2 verify: a transaction cannot be persisted
without both hashes).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SnapshotReason(str, enum.Enum):
    """Why a workspace snapshot was taken. Extend, don't repurpose, a value."""

    BEFORE_CODE_CHANGE = "before_code_change"
    BEFORE_CMS_CHANGE = "before_cms_change"
    # Guided fix workflow: one-file immutable original captured before the
    # user-triggered fix is applied. Row-level snapshot of only the files
    # the fix touches (files_json), not a whole-workspace copy.
    BEFORE_GUIDED_FIX = "before_guided_fix"


class Snapshot(Base):
    """A pre-modification copy of the workspace (step 7.2, `[SPEC AGENTS.md §33]`).

    `snapshot_path` holds a raw file copy (excluding `.git`) alongside
    `commit_hash`, so a rejected/failed change can be restored file-for-file
    without depending on the workspace's git state. This is a plain
    restore point, not Phase 8's AST-aware semantic rollback.
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[SnapshotReason] = mapped_column(
        Enum(
            SnapshotReason,
            name="snapshot_reason",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    files_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ValidationRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"


class ValidationCheckType(str, enum.Enum):
    """Validation-layer categories `[SPEC AGENTS.md §35]`, plus the two
    pre-write gates (step 7.4/7.9) recorded here so a stop is durable and
    inspectable rather than an exception swallowed upstream.
    """

    SCOPE = "scope"
    CONTENT = "content"
    LINT = "lint"
    TYPECHECK = "typecheck"
    BUILD = "build"
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    BROWSER = "browser"
    SEO = "seo"
    AEO = "aeo"
    GEO = "geo"
    AGENT_ACCESSIBILITY = "agent_accessibility"
    REGRESSION = "regression"


class ValidationCheckStatus(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


class ValidationRun(Base):
    """One validation attempt for one Code Agent run (step 7.6/7.7)."""

    __tablename__ = "validation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ValidationRunStatus] = mapped_column(
        Enum(
            ValidationRunStatus,
            name="validation_run_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=ValidationRunStatus.PENDING.value,
        nullable=False,
    )
    affected_urls_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    gaps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ValidationResult(Base):
    """One individual check within a `ValidationRun`."""

    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    validation_run_id: Mapped[int] = mapped_column(
        ForeignKey("validation_runs.id", ondelete="CASCADE"), nullable=False
    )
    check_type: Mapped[ValidationCheckType] = mapped_column(
        Enum(
            ValidationCheckType,
            name="validation_check_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[ValidationCheckStatus] = mapped_column(
        Enum(
            ValidationCheckStatus,
            name="validation_check_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ChangeSetStatus(str, enum.Enum):
    APPLIED = "applied"
    PARTIALLY_ROLLED_BACK = "partially_rolled_back"
    ROLLED_BACK = "rolled_back"


class ChangePlatform(str, enum.Enum):
    """`[SPEC AGENTS.md §29]` — WordPress extends this value set, it never replaces it.
    """

    GIT = "git"
    WORDPRESS = "wordpress"


class ChangeTransactionStatus(str, enum.Enum):
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class ChangeItemKind(str, enum.Enum):
    """Semantic rollback units `[SPEC AGENTS.md §37]`, narrowed to the ones
    this module can actually locate deterministically. `commit` and
    `Change Set` are the coarser granularities `ChangeTransaction`/
    `ChangeSet` themselves already represent.
    """

    FILE = "file"
    FUNCTION = "function"
    CLASS_ = "class"
    COMPONENT = "component"
    PARAGRAPH = "paragraph"
    OTHER = "other"


class ChangeItemStatus(str, enum.Enum):
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class ChangeSet(Base):
    """Groups the Change Transactions from one Code Agent run `[SPEC AGENTS.md §30]`.

    One Change Set per approved `code_change` job in v1 (one Finding in,
    one or more changed files out) — `finding_ids_json` is a list because
    the spec's own Change Set shape is multi-finding, even though today's
    Change Planner only ever plans against a single Finding.
    """

    __tablename__ = "change_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    finding_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    affected_resources_json: Mapped[list] = mapped_column(JSON, nullable=False)
    risk: Mapped[str] = mapped_column(Text, nullable=False)
    validation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("validation_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ChangeSetStatus] = mapped_column(
        Enum(
            ChangeSetStatus,
            name="change_set_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=ChangeSetStatus.APPLIED.value,
        nullable=False,
    )
    # Phase 9 fills these once a Change Set becomes a real commit/PR.
    git_commit_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pull_request_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rollback_info_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    baseline_metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    treatment_metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChangeTransaction(Base):
    """One platform-level mutation `[SPEC AGENTS.md §29]`: one changed
    resource (a file, in v1's Git-only platform), grouped under a
    `ChangeSet`. `hash_before`/`hash_after` are required at the ORM layer
    and re-checked in `app.changes.transaction.record_transaction` before
    any row is ever added to the session (step 8.2 verify).
    """

    __tablename__ = "change_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True
    )
    finding_id: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[ChangePlatform] = mapped_column(
        Enum(
            ChangePlatform,
            name="change_platform",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    resource: Mapped[str] = mapped_column(String(1024), nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSON, nullable=False)
    validation_status: Mapped[ValidationRunStatus] = mapped_column(
        Enum(
            ValidationRunStatus,
            name="validation_run_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    rollback_method: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ChangeTransactionStatus] = mapped_column(
        Enum(
            ChangeTransactionStatus,
            name="change_transaction_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=ChangeTransactionStatus.APPLIED.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["ChangeItem"]] = relationship(
        back_populates="change_transaction", cascade="all, delete-orphan"
    )


class ChangeItem(Base):
    """One semantic unit within a `ChangeTransaction` `[SPEC AGENTS.md §37]`.

    `content_before`/`content_after` are stored for anything finer than
    `file` (function/class/component/paragraph blocks are small by
    construction); a `file`-kind item leaves them `None` and relies on the
    parent transaction's `Snapshot` for whole-file restore, so a full file
    is never duplicated into this table.
    """

    __tablename__ = "change_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("change_transactions.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ChangeItemKind] = mapped_column(
        Enum(
            ChangeItemKind,
            name="change_item_kind",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    content_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ChangeItemStatus] = mapped_column(
        Enum(
            ChangeItemStatus,
            name="change_item_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=ChangeItemStatus.APPLIED.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    change_transaction: Mapped[ChangeTransaction] = relationship(back_populates="items")


class RollbackTargetType(str, enum.Enum):
    CHANGE_SET = "change_set"
    CHANGE_TRANSACTION = "change_transaction"
    CHANGE_ITEM = "change_item"


class RollbackOperationStatus(str, enum.Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    APPLIED = "applied"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RollbackOperation(Base):
    """One rollback request/attempt `[SPEC AGENTS.md §38]`.

    Always created before anything is written back, even when
    `requires_confirmation` stops short of applying — so a STOP is a
    durable, inspectable row, the same posture `ValidationRun`/`Snapshot`
    already take for the forward path.
    """

    __tablename__ = "rollback_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[RollbackTargetType] = mapped_column(
        Enum(
            RollbackTargetType,
            name="rollback_target_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    change_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_sets.id", ondelete="SET NULL"), nullable=True
    )
    change_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_transactions.id", ondelete="SET NULL"), nullable=True
    )
    change_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_items.id", ondelete="SET NULL"), nullable=True
    )
    requested_target: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_reasons_json: Mapped[list] = mapped_column(JSON, nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[RollbackOperationStatus] = mapped_column(
        Enum(
            RollbackOperationStatus,
            name="rollback_operation_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=RollbackOperationStatus.PENDING_CONFIRMATION.value,
        nullable=False,
    )
    result_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
