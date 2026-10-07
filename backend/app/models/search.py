"""`search_console_data` and `experiments` — Phase 11 (steps 11.2, 11.4).

`search_console_data` is the live GSC snapshot (queries, pages,
impressions, clicks, CTR, position, date range). Experiments store the
`[SPEC]` model Hypothesis → Baseline → Change → Validation →
Post-change measurement → Result. Result rows never claim causation.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SearchConsoleDimension(str, enum.Enum):
    PAGE = "page"
    QUERY = "query"
    PAGE_QUERY = "page_query"
    DATE = "date"
    DEVICE = "device"
    COUNTRY = "country"


class ExperimentType(str, enum.Enum):
    """Candidate experiment kinds `[SPEC AGENTS.md §48]`."""

    TITLE = "title"
    FAQ = "faq"
    CONTENT_RESTRUCTURE = "content_restructure"
    SCHEMA = "schema"
    INTERNAL_LINKS = "internal_links"


class ExperimentStatus(str, enum.Enum):
    """Stages of the experiment model `[SPEC AGENTS.md §48]`."""

    HYPOTHESIS = "hypothesis"
    BASELINE = "baseline"
    CHANGE = "change"
    VALIDATION = "validation"
    POST_CHANGE_MEASUREMENT = "post_change_measurement"
    RESULT = "result"


class SearchConsoleRow(Base):
    __tablename__ = "search_console_data"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    website_id: Mapped[int | None] = mapped_column(
        ForeignKey("websites.id", ondelete="SET NULL"), nullable=True
    )
    dimension: Mapped[SearchConsoleDimension] = mapped_column(
        Enum(
            SearchConsoleDimension,
            name="search_console_dimension",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    query: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    page: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, nullable=False)
    position: Mapped[float] = mapped_column(Float, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    change_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_sets.id", ondelete="SET NULL"), nullable=True
    )
    experiment_type: Mapped[ExperimentType] = mapped_column(
        Enum(
            ExperimentType,
            name="experiment_type",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    change_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    baseline_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    treatment_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(
            ExperimentStatus,
            name="experiment_status",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        server_default=ExperimentStatus.HYPOTHESIS.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    baseline_recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    treatment_recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
