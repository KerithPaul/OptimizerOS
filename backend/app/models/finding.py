"""`analysis_runs`, `findings` — checkpoint 5.B.1.

`AnalysisRun` is one audit of a project. Status `partial` means a claimed
input or store was missing; the run must name that gap and must not be
treated as a complete healthy audit.

`Finding` is the persisted Evidence Engine record plus the Finding Model
fields `[SPEC AGENTS.md §23–§24]`. Application code refuses to persist a
row whose evidence list is empty (step 5.B.2 verify).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.knowledge.authority import AuthorityLevel
from app.models.knowledge import RuleCategory, RuleConfidence, RuleSeverity


class AnalysisRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class FindingStatus(str, enum.Enum):
    """Finding statuses `[SPEC AGENTS.md §24]`."""

    OPEN = "OPEN"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    FIXED = "FIXED"
    ROLLED_BACK = "ROLLED_BACK"


def _authority_column() -> Enum:
    return Enum(
        AuthorityLevel,
        name="authority_level",
        native_enum=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class AnalysisRun(Base):
    """One audit job's durable result.

    `gaps_json` lists missing inputs/capabilities. `inputs_json` lists what
    was actually present so a partial run cannot claim inputs it did not
    have. `scores_json` is the slot 5.C writes; 5.B persists the column.
    """

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    crawl_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AnalysisRunStatus] = mapped_column(
        Enum(
            AnalysisRunStatus,
            name="analysis_run_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=AnalysisRunStatus.PENDING.value,
        nullable=False,
    )
    inputs_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gaps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scores_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Finding(Base):
    """One grounded finding for an analysis run."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id", "finding_id", name="uq_findings_analysis_run_finding_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[str] = mapped_column(String(200), nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_authority: Mapped[AuthorityLevel] = mapped_column(
        _authority_column(), nullable=False
    )
    rule: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[RuleCategory] = mapped_column(
        Enum(
            RuleCategory,
            name="rule_category",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    severity: Mapped[RuleSeverity] = mapped_column(
        Enum(
            RuleSeverity,
            name="rule_severity",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    confidence: Mapped[RuleConfidence] = mapped_column(
        Enum(
            RuleConfidence,
            name="rule_confidence",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    affected_resource: Mapped[str] = mapped_column(String(2048), nullable=False)
    affected_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    affected_code_entity: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    expected_mechanism: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    actionability: Mapped[str] = mapped_column(String(50), nullable=False)
    risk: Mapped[str] = mapped_column(Text, nullable=False)
    will_validate: Mapped[str] = mapped_column(Text, nullable=False)
    change_worked: Mapped[str] = mapped_column(Text, nullable=False)
    rollback: Mapped[str] = mapped_column(Text, nullable=False)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach_input: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[FindingStatus] = mapped_column(
        Enum(
            FindingStatus,
            name="finding_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=FindingStatus.OPEN.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
