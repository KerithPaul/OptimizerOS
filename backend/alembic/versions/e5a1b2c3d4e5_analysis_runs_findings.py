"""analysis_runs, findings

Revision ID: e5a1b2c3d4e5
Revises: 9448d2f7a494
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "9448d2f7a494"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUTHORITY_LEVELS = (
    "official_standard",
    "official_vendor_docs",
    "peer_reviewed_research",
    "reputable_practitioner",
    "community_unverified",
)


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("crawl_run_id", sa.Integer(), nullable=True),
        sa.Column("repository_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "succeeded",
                "failed",
                "partial",
                name="analysis_run_status",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("inputs_json", sa.JSON(), nullable=True),
        sa.Column("gaps_json", sa.JSON(), nullable=True),
        sa.Column("scores_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("finding_id", sa.String(length=200), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "source_authority",
            sa.Enum(*_AUTHORITY_LEVELS, name="authority_level"),
            nullable=False,
        ),
        sa.Column("rule", sa.String(length=100), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "technical_seo",
                "content_seo",
                "aeo",
                "geo",
                name="rule_category",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("low", "medium", "high", "critical", name="rule_severity"),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Enum("low", "medium", "high", name="rule_confidence"),
            nullable=False,
        ),
        sa.Column("affected_resource", sa.String(length=2048), nullable=False),
        sa.Column("affected_url", sa.String(length=2048), nullable=True),
        sa.Column("affected_code_entity", sa.String(length=1024), nullable=True),
        sa.Column("expected_mechanism", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("actionability", sa.String(length=50), nullable=False),
        sa.Column("risk", sa.Text(), nullable=False),
        sa.Column("will_validate", sa.Text(), nullable=False),
        sa.Column("change_worked", sa.Text(), nullable=False),
        sa.Column("rollback", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "PLANNED",
                "IN_PROGRESS",
                "VALIDATED",
                "REJECTED",
                "FIXED",
                "ROLLED_BACK",
                name="finding_status",
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id", "finding_id", name="uq_findings_analysis_run_finding_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("findings")
    op.drop_table("analysis_runs")
    sa.Enum(name="finding_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="analysis_run_status").drop(op.get_bind(), checkfirst=True)
