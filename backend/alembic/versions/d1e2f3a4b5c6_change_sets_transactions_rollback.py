"""change_sets, change_transactions, change_items, rollback_operations

Revision ID: d1e2f3a4b5c6
Revises: b8c4d2e6f1a3
Create Date: 2026-09-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "b8c4d2e6f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("finding_ids_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("affected_resources_json", sa.JSON(), nullable=False),
        sa.Column("risk", sa.Text(), nullable=False),
        sa.Column("validation_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("applied", "partially_rolled_back", "rolled_back", name="change_set_status"),
            server_default="applied",
            nullable=False,
        ),
        sa.Column("git_commit_ref", sa.String(length=255), nullable=True),
        sa.Column("pull_request_ref", sa.String(length=255), nullable=True),
        sa.Column("rollback_info_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "change_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_set_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("finding_id", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.Enum("git", name="change_platform"), nullable=False),
        sa.Column("resource", sa.String(length=1024), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=False),
        sa.Column("hash_before", sa.String(length=64), nullable=False),
        sa.Column("hash_after", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column(
            "validation_status",
            sa.Enum("pending", "running", "passed", "failed", "partial", name="validation_run_status"),
            nullable=False,
        ),
        sa.Column("rollback_method", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("applied", "rolled_back", name="change_transaction_status"),
            server_default="applied",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "change_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("change_transaction_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("file", "function", "class", "component", "paragraph", "other", name="change_item_kind"),
            nullable=False,
        ),
        sa.Column("symbol_name", sa.String(length=255), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("hash_before", sa.String(length=64), nullable=False),
        sa.Column("hash_after", sa.String(length=64), nullable=False),
        sa.Column("content_before", sa.Text(), nullable=True),
        sa.Column("content_after", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("applied", "rolled_back", name="change_item_status"),
            server_default="applied",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["change_transaction_id"], ["change_transactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "rollback_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum("change_set", "change_transaction", "change_item", name="rollback_target_type"),
            nullable=False,
        ),
        sa.Column("change_set_id", sa.Integer(), nullable=True),
        sa.Column("change_transaction_id", sa.Integer(), nullable=True),
        sa.Column("change_item_id", sa.Integer(), nullable=True),
        sa.Column("requested_target", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_reasons_json", sa.JSON(), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_confirmation", "applied", "failed", "cancelled",
                name="rollback_operation_status",
            ),
            server_default="pending_confirmation",
            nullable=False,
        ),
        sa.Column("result_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["change_transaction_id"], ["change_transactions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["change_item_id"], ["change_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("rollback_operations")
    op.drop_table("change_items")
    op.drop_table("change_transactions")
    op.drop_table("change_sets")
    sa.Enum(name="rollback_operation_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="rollback_target_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="change_item_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="change_item_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="change_transaction_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="change_platform").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="change_set_status").drop(op.get_bind(), checkfirst=True)
