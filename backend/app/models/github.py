"""`commits`, `pull_requests` — Migration 9 (step 9.5).

A Change Set remains the durable mutation record. These rows are the
GitHub-side projection of one logical commit and at most one PR per
Change Set — never one PR per tiny file.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CommitStatus(str, enum.Enum):
    CREATED = "created"
    PUSHED = "pushed"
    FAILED = "failed"


class PullRequestState(str, enum.Enum):
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"
    FAILED = "failed"


class CiStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class GithubCommit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("change_set_id", name="uq_commits_change_set_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    files_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[CommitStatus] = mapped_column(
        Enum(
            CommitStatus,
            name="github_commit_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class GithubPullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("change_set_id", name="uq_pull_requests_change_set_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("change_sets.id", ondelete="CASCADE"), nullable=False
    )
    commit_id: Mapped[int | None] = mapped_column(
        ForeignKey("commits.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PullRequestState] = mapped_column(
        Enum(
            PullRequestState,
            name="github_pull_request_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    ci_status: Mapped[CiStatus] = mapped_column(
        Enum(
            CiStatus,
            name="github_ci_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=CiStatus.UNKNOWN.value,
        nullable=False,
    )
    ci_detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
