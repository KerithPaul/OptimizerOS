"""`repositories` — Phase 2, step 2.B.1.

One git remote per project in v1 (`project_id` is unique). Clone status is
constrained at the schema level. Architecture profile is JSON produced by
the deterministic profiler (step 2.B.4).
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CloneStatus(str, enum.Enum):
    PENDING = "pending"
    CLONING = "cloning"
    CLONED = "cloned"
    CLONE_FAILED = "clone_failed"


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("project_id", name="uq_repositories_project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    default_branch: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="main"
    )
    cloned_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    clone_status: Mapped[CloneStatus] = mapped_column(
        Enum(
            CloneStatus,
            name="clone_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=CloneStatus.PENDING.value,
        nullable=False,
    )
    last_indexed_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    architecture_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    clone_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
