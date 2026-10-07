"""`agent_runs`, `agent_messages` — checkpoint 6.1.

One `AgentRun` row per individual agent execution (Research, SEO, AEO, GEO,
Code, or Reviewer); several rows share one `job_id` when a single
natural-language request fans out into the Research Agent plus one or more
of SEO/AEO/GEO, or when a `code_change` job runs the Code Agent and the
Reviewer Agent one after another (step 7.3/7.8).
`AgentRunStatus.PARTIAL` means a budget limit — or, for the Code Agent, a
scope/content violation (step 7.4/7.9) — stopped the run before it
finished `[SPEC AGENTS.md §51]` — this is not a failure, it is the
mandated STOP-and-report-partial-progress behaviour.

Token accounting for agent-originated LLM calls lives on `agent_messages`,
not on `jobs.llm_usage_json` (step 6.1: "Token accounting moves here from
jobs").

`request_text` and `objective_json` are duplicated across every AgentRun
row that shares a `job_id` `[PROPOSED]`: they belong to the pipeline, not
to one agent, but the plan calls for exactly two new tables here, so the
pipeline-level facts ride along on each row instead of a third table.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentType(str, enum.Enum):
    """The four Optimization Agents `[SPEC AGENTS.md §27]`, plus the Phase 7
    Code Agent and Reviewer Agent (step 7.3/7.8). Both reuse `agent_runs` /
    `agent_messages` rather than growing bespoke tables — Phase 7's plan
    (§13, Migration 7) only adds `snapshots`, `validation_runs`, and
    `validation_results`.
    """

    RESEARCH = "research"
    SEO = "seo"
    AEO = "aeo"
    GEO = "geo"
    CODE = "code"
    REVIEWER = "reviewer"
    CMS = "cms"


class AgentRunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentMessageRole(str, enum.Enum):
    SYSTEM = "system"
    TOOL = "tool"
    ASSISTANT = "assistant"


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    agent_type: Mapped[AgentType] = mapped_column(
        Enum(
            AgentType,
            name="agent_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(
            AgentRunStatus,
            name="agent_run_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=AgentRunStatus.RUNNING.value,
        nullable=False,
    )
    request_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    iterations_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_modified: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    stopped_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[AgentMessageRole] = mapped_column(
        Enum(
            AgentMessageRole,
            name="agent_message_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
