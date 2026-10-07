"""`projects` — Phase 1. Mode defaults to AUDIT_ONLY at the schema level (CONFIRMED C3)."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectMode(str, enum.Enum):
    """The five execution modes (IMPLEMENTATION_PLAN_V2.md §1.6)."""

    AUDIT_ONLY = "AUDIT_ONLY"
    SUGGEST_ONLY = "SUGGEST_ONLY"
    APPLY_LOCALLY = "APPLY_LOCALLY"
    COMMIT = "COMMIT"
    CREATE_PR = "CREATE_PR"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[ProjectMode] = mapped_column(
        Enum(
            ProjectMode,
            name="project_mode",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=ProjectMode.AUDIT_ONLY.value,
        nullable=False,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
