"""site_reports and metric_snapshots

Revision ID: e8a1b2c3d4e5
Revises: de69f2426879
Create Date: 2026-10-01

Slice 1 of the site health report job: persist assembled reports and
metric snapshots so a manager can re-read a run after the job finishes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "de69f2426879"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("analysis_run_id", sa.Integer(), nullable=True),
        sa.Column("crawl_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                name="site_report_status",
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column("gaps_json", sa.JSON(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("deltas_json", sa.JSON(), nullable=True),
        sa.Column("document_json", sa.JSON(), nullable=True),
        sa.Column("html", sa.Text(), nullable=True),
        sa.Column(
            "email_status",
            sa.Enum("unavailable", "sent", "failed", name="site_report_email_status"),
            server_default="unavailable",
            nullable=False,
        ),
        sa.Column("email_detail", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_site_reports_project_id", "site_reports", ["project_id"])

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("site_report_id", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_report_id"], ["site_reports.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metric_snapshots_project_id", "metric_snapshots", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_metric_snapshots_project_id", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
    op.drop_index("ix_site_reports_project_id", table_name="site_reports")
    op.drop_table("site_reports")
    sa.Enum(name="site_report_email_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="site_report_status").drop(op.get_bind(), checkfirst=True)
