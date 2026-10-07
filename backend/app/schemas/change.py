"""Typed request/response models for the changes API (step 7.10, step 8.7)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, model_validator

from app.models.agent import AgentRunStatus
from app.models.change import (
    ChangeItemKind,
    ChangeItemStatus,
    ChangePlatform,
    ChangeSetStatus,
    ChangeTransactionStatus,
    RollbackOperationStatus,
    RollbackTargetType,
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationRunStatus,
)


class ApplyChangeCreate(BaseModel):
    finding_id: str
    source_agent_run_id: int


class SnapshotFileCreate(BaseModel):
    """Guided-fix STEP 3: the file whose exact pre-modification content to capture."""

    file_path: str


class ChangeJobOut(BaseModel):
    job_id: int
    dry_run: bool


class ValidationResultOut(BaseModel):
    id: int
    check_type: ValidationCheckType
    status: ValidationCheckStatus
    detail: str | None
    duration_ms: int | None

    model_config = {"from_attributes": True}


class ValidationRunOut(BaseModel):
    id: int
    status: ValidationRunStatus
    affected_urls_json: list | None
    gaps_json: list | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    results: list[ValidationResultOut]

    model_config = {"from_attributes": True}


class ChangeSummaryOut(BaseModel):
    """One row in the project's change list — the latest Code Agent run per finding."""

    agent_run_id: int
    job_id: int | None
    finding_id: str
    status: AgentRunStatus
    dry_run: bool | None
    stopped_reason: str | None
    approved: bool | None
    created_at: datetime
    finished_at: datetime | None


class ChangeFindingOut(BaseModel):
    """The Finding fields the change-review UI must show (step 7.10)."""

    observation: str
    evidence: list
    risk: str
    recommended_action: str
    affected_resource: str
    affected_url: str | None
    affected_code_entity: str | None
    expected_mechanism: str
    status: str


class ChangeDetailOut(BaseModel):
    """Backs the change review UI (step 7.10): plan, diff, validation, review."""

    agent_run_id: int
    job_id: int | None
    finding_id: str
    status: AgentRunStatus
    error: str | None
    dry_run: bool | None
    stopped_reason: str | None
    violation: dict | None
    change_plan: dict | None
    execution_plan: dict | None
    validation_plan: dict | None
    diff_preview: dict | None
    validation_run: ValidationRunOut | None
    reviewer_verdict: dict | None
    approved: bool | None
    finding_status: str
    finding: ChangeFindingOut | None = None
    validation_skipped: bool | None = None
    reviewer_skipped: bool | None = None
    workspace_files: list | None = None
    platform_notes: list[str] | None = None
    repair_attempts: int | None = None


class ChangeItemOut(BaseModel):
    id: int
    kind: ChangeItemKind
    symbol_name: str | None
    start_line: int | None
    end_line: int | None
    status: ChangeItemStatus
    created_at: datetime
    rolled_back_at: datetime | None

    model_config = {"from_attributes": True}


class ChangeTransactionOut(BaseModel):
    id: int
    finding_id: str
    platform: ChangePlatform
    resource: str
    field: str
    hash_before: str
    hash_after: str
    reason: str
    validation_status: ValidationRunStatus
    rollback_method: str
    status: ChangeTransactionStatus
    created_at: datetime
    rolled_back_at: datetime | None
    items: list[ChangeItemOut]

    model_config = {"from_attributes": True}


class ChangeSetSummaryOut(BaseModel):
    id: int
    objective: str
    finding_ids_json: list
    affected_resources_json: list
    status: ChangeSetStatus
    created_at: datetime
    applied_at: datetime | None

    model_config = {"from_attributes": True}


class ChangeSetDetailOut(BaseModel):
    id: int
    objective: str
    description: str | None
    finding_ids_json: list
    evidence_json: list | None
    affected_resources_json: list
    risk: str
    status: ChangeSetStatus
    git_commit_ref: str | None
    pull_request_ref: str | None
    rollback_info_json: dict | None
    baseline_metrics_json: dict | None = None
    treatment_metrics_json: dict | None = None
    created_at: datetime
    applied_at: datetime | None
    transactions: list[ChangeTransactionOut]

    model_config = {"from_attributes": True}


class RollbackTargetIn(BaseModel):
    """Exactly one selector must be set `[SPEC AGENTS.md §37]`'s example
    requests each map to one of these:

    - `change_set_id` -- "Rollback Change Set #42."
    - `change_transaction_id` -- roll back one file within a set.
    - `change_item_id` -- "Undo only the paragraph ArchitectOS changed."
    - `symbol_name` -- "Rollback only PricingTable." / "Restore the
      previous version of this component."
    - `category` -- "Rollback the last SEO change." (`seo`, `aeo`, `geo`,
      or a raw `RuleCategory` value)
    """

    change_set_id: int | None = None
    change_transaction_id: int | None = None
    change_item_id: int | None = None
    symbol_name: str | None = None
    category: str | None = None
    confirmed: bool = False

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> "RollbackTargetIn":
        selectors = [
            self.change_set_id,
            self.change_transaction_id,
            self.change_item_id,
            self.symbol_name,
            self.category,
        ]
        if sum(1 for value in selectors if value is not None) != 1:
            raise ValueError("exactly one of change_set_id, change_transaction_id, "
                              "change_item_id, symbol_name, category must be given")
        return self


class RollbackOperationOut(BaseModel):
    id: int
    target_type: RollbackTargetType
    change_set_id: int | None
    change_transaction_id: int | None
    change_item_id: int | None
    requested_target: str
    method: str
    confidence: float
    confidence_reasons_json: list
    requires_confirmation: bool
    status: RollbackOperationStatus
    result_detail: str | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
