"""snapshots, validation_runs, validation_results; agent_type + code/reviewer

Revision ID: b8c4d2e6f1a3
Revises: a7c9d1e2f3b4
Create Date: 2026-09-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c4d2e6f1a3"
down_revision: Union[str, Sequence[str], None] = "a7c9d1e2f3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_runs MODIFY COLUMN agent_type "
        "ENUM('research','seo','aeo','geo','code','reviewer') NOT NULL"
    )

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column(
            "reason",
            sa.Enum("before_code_change", name="snapshot_reason"),
            nullable=False,
        ),
        sa.Column("commit_hash", sa.String(length=64), nullable=True),
        sa.Column("snapshot_path", sa.String(length=1024), nullable=False),
        sa.Column("files_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "validation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "passed", "failed", "partial", name="validation_run_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("affected_urls_json", sa.JSON(), nullable=True),
        sa.Column("gaps_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "validation_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("validation_run_id", sa.Integer(), nullable=False),
        sa.Column(
            "check_type",
            sa.Enum(
                "scope",
                "content",
                "lint",
                "typecheck",
                "build",
                "unit_test",
                "integration_test",
                "browser",
                "seo",
                "aeo",
                "geo",
                "regression",
                name="validation_check_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("passed", "failed", "skipped", name="validation_check_status"),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("validation_results")
    op.drop_table("validation_runs")
    op.drop_table("snapshots")
    sa.Enum(name="validation_check_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="validation_check_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="validation_run_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="snapshot_reason").drop(op.get_bind(), checkfirst=True)

    op.execute("DELETE FROM agent_runs WHERE agent_type IN ('code','reviewer')")
    op.execute(
        "ALTER TABLE agent_runs MODIFY COLUMN agent_type "
        "ENUM('research','seo','aeo','geo') NOT NULL"
    )
