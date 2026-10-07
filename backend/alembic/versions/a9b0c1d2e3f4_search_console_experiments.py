"""search_console_data, experiments; finding reach + change-set metrics

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-09-17

Phase 11: live Google Search Console rows, experiment records, finding
reach-input/impressions, and Change Set baseline/treatment metrics.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_console_data",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=True),
        sa.Column("website_id", sa.Integer(), nullable=True),
        sa.Column(
            "dimension",
            sa.Enum("page", "query", "page_query", name="search_console_dimension"),
            nullable=False,
        ),
        sa.Column("query", sa.String(length=2048), nullable=True),
        sa.Column("page", sa.String(length=2048), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("ctr", sa.Float(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["website_id"], ["websites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("change_set_id", sa.Integer(), nullable=True),
        sa.Column(
            "experiment_type",
            sa.Enum(
                "title",
                "faq",
                "content_restructure",
                "schema",
                "internal_links",
                name="experiment_type",
            ),
            nullable=False,
        ),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("change_json", sa.JSON(), nullable=True),
        sa.Column("baseline_metrics", sa.JSON(), nullable=True),
        sa.Column("treatment_metrics", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "hypothesis",
                "baseline",
                "change",
                "validation",
                "post_change_measurement",
                "result",
                name="experiment_status",
            ),
            server_default="hypothesis",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("baseline_recorded_at", sa.DateTime(), nullable=True),
        sa.Column("treatment_recorded_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("findings", sa.Column("impressions", sa.Integer(), nullable=True))
    op.add_column("findings", sa.Column("reach_input", sa.String(length=50), nullable=True))
    op.add_column("change_sets", sa.Column("baseline_metrics_json", sa.JSON(), nullable=True))
    op.add_column("change_sets", sa.Column("treatment_metrics_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("change_sets", "treatment_metrics_json")
    op.drop_column("change_sets", "baseline_metrics_json")
    op.drop_column("findings", "reach_input")
    op.drop_column("findings", "impressions")
    op.drop_table("experiments")
    op.drop_table("search_console_data")
    sa.Enum(name="experiment_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="experiment_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="search_console_dimension").drop(op.get_bind(), checkfirst=True)
