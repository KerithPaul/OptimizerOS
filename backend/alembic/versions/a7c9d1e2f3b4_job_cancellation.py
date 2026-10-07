"""job cancellation: jobs.cancel_requested + agent_run_status 'cancelled'

Revision ID: a7c9d1e2f3b4
Revises: f6b1c2d3e4a5
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c9d1e2f3b4"
down_revision: Union[str, Sequence[str], None] = "f6b1c2d3e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("cancel_requested", sa.Boolean(), server_default="0", nullable=False),
    )
    op.execute(
        "ALTER TABLE agent_runs MODIFY COLUMN status "
        "ENUM('running','succeeded','partial','failed','cancelled') "
        "NOT NULL DEFAULT 'running'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE agent_runs SET status = 'failed' WHERE status = 'cancelled'"
    )
    op.execute(
        "ALTER TABLE agent_runs MODIFY COLUMN status "
        "ENUM('running','succeeded','partial','failed') "
        "NOT NULL DEFAULT 'running'"
    )
    op.drop_column("jobs", "cancel_requested")
