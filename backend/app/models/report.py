"""Site health reports and metric snapshots for scheduled manager reports."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiteReportStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class SiteReportEmailStatus(str, enum.Enum):
    UNAVAILABLE = "unavailable"
    SENT = "sent"
    FAILED = "failed"


def _status_column() -> Enum:
    return Enum(
        SiteReportStatus,
        name="site_report_status",
        native_enum=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


def _email_status_column() -> Enum:
    return Enum(
        SiteReportEmailStatus,
        name="site_report_email_status",
        native_enum=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class SiteReport(Base):
    __tablename__ = "site_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    crawl_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[SiteReportStatus] = mapped_column(
        _status_column(),
        server_default=SiteReportStatus.RUNNING.value,
        nullable=False,
    )
    gaps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deltas_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    document_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_status: Mapped[SiteReportEmailStatus] = mapped_column(
        _email_status_column(),
        server_default=SiteReportEmailStatus.UNAVAILABLE.value,
        nullable=False,
    )
    email_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MetricSnapshot(Base):
    """One capture_metrics() payload per site report, used for period deltas."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    site_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("site_reports.id", ondelete="SET NULL"), nullable=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
