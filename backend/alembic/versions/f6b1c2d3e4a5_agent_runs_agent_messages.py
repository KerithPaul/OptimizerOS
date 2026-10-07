"""agent_runs, agent_messages

Revision ID: f6b1c2d3e4a5
Revises: e5a1b2c3d4e5
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b1c2d3e4a5"
down_revision: Union[str, Sequence[str], None] = "e5a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column(
            "agent_type",
            sa.Enum("research", "seo", "aeo", "geo", name="agent_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("running", "succeeded", "partial", "failed", name="agent_run_status"),
            server_default="running",
            nullable=False,
        ),
        sa.Column("request_text", sa.Text(), nullable=True),
        sa.Column("objective_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("iterations_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tool_calls_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_modified", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stopped_reason", sa.String(length=50), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("system", "tool", "assistant", name="agent_message_role"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("agent_messages")
    op.drop_table("agent_runs")
    sa.Enum(name="agent_message_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="agent_run_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="agent_type").drop(op.get_bind(), checkfirst=True)
