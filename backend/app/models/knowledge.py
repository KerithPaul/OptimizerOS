"""`optimization_rules`, `optimization_sources` — checkpoint 4.A.

`OptimizationSource` is the attributed-source registry: one row per distinct
source, carrying its current authority ranking (step 4.A.2). `OptimizationRule`
is one versioned knowledge record `[SPEC AGENTS.md §18]`. Its `source`,
`source_url`, and `authority` columns are a snapshot taken at ingestion time,
not a live join to `optimization_sources` — if a source is later re-ranked,
rules already ingested from it keep the authority they were evaluated under,
matching the `[SPEC]` rule that historical audits keep the rule version they
used and are never retroactively rewritten.
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


class RuleCategory(str, enum.Enum):
    TECHNICAL_SEO = "technical_seo"
    CONTENT_SEO = "content_seo"
    AEO = "aeo"
    GEO = "geo"
    AGENT_ACCESSIBILITY = "agent_accessibility"


class RuleSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleConfidence(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _authority_column() -> Enum:
    return Enum(
        AuthorityLevel,
        name="authority_level",
        native_enum=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class OptimizationSource(Base):
    """Registry of attributed knowledge sources and their current authority.

    Re-ranking a source here (e.g. a vendor page is deprecated) never touches
    rules already ingested from it — see `OptimizationRule.authority`.

    `source_url` is VARCHAR(2048) and is not unique-indexed: utf8mb4 makes it
    exceed MySQL's 3072-byte key limit, the same constraint `WebsitePage.url`
    documents. Dedup on ingest (step 4.B.2) belongs to the ingest job.
    """

    __tablename__ = "optimization_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    authority: Mapped[AuthorityLevel] = mapped_column(_authority_column(), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class OptimizationRule(Base):
    """One versioned optimization-knowledge record `[SPEC AGENTS.md §18]`.

    `(rule_id, version)` is unique: re-ingesting unchanged content must not
    fork a new version (step 4.B.2), and a rule hit's `rule_version`
    (step 4.C.1) always resolves to the exact row it was evaluated against.
    """

    __tablename__ = "optimization_rules"
    __table_args__ = (
        UniqueConstraint(
            "rule_id", "version", name="uq_optimization_rules_rule_id_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[RuleCategory] = mapped_column(
        Enum(
            RuleCategory,
            name="rule_category",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("optimization_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    authority: Mapped[AuthorityLevel] = mapped_column(_authority_column(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[dict] = mapped_column(JSON, nullable=False)
    severity: Mapped[RuleSeverity] = mapped_column(
        Enum(
            RuleSeverity,
            name="rule_severity",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[RuleConfidence] = mapped_column(
        Enum(
            RuleConfidence,
            name="rule_confidence",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
