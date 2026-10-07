"""`websites`, `pages`, `crawl_runs`, `platform_connections` — checkpoint 3.A.4.

GitHub PAT (Phase 9) and WordPress credentials (Phase 10) are stored
encrypted here. GitHub connections have `website_id=NULL`. Capability JSON is the
persisted report the mode validator reads (step 3.A.3). One website per
project in v1, matching repositories.
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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CrawlRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class Website(Base):
    __tablename__ = "websites"
    __table_args__ = (UniqueConstraint("project_id", name="uq_websites_project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    __table_args__ = (
        UniqueConstraint("website_id", name="uq_platform_connections_website_id"),
        UniqueConstraint(
            "project_id", "platform", name="uq_platform_connections_project_platform"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    website_id: Mapped[int | None] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False)
    capabilities_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    credentials_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    website_id: Mapped[int] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[CrawlRunStatus] = mapped_column(
        Enum(
            CrawlRunStatus,
            name="crawl_run_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=CrawlRunStatus.PENDING.value,
        nullable=False,
    )
    stats_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cap_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebsitePage(Base):
    """ORM row for `pages`. The Common Website Model lives in `model_json`.

    `url` is VARCHAR(2048) and is not unique-indexed: utf8mb4 makes
    (crawl_run_id, url) exceed MySQL's 3072-byte key limit. Duplicate
    detection belongs to the crawler (3.B).
    """

    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    website_id: Mapped[int] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), nullable=False
    )
    crawl_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    canonical: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    model_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
