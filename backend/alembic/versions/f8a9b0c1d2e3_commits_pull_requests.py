"""commits, pull_requests; GitHub PAT on platform_connections

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-17

Phase 9: encrypted GitHub PAT lives on platform_connections with a
nullable website_id (GitHub is project-scoped, not website-scoped).
One logical commit and at most one PR per Change Set.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "platform_connections",
        "website_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_unique_constraint(
        "uq_platform_connections_project_platform",
        "platform_connections",
        ["project_id", "platform"],
    )

    op.create_table(
        "commits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("change_set_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("sha", sa.String(length=64), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("files_json", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("created", "pushed", "failed", name="github_commit_status"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("change_set_id", name="uq_commits_change_set_id"),
    )

    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("change_set_id", sa.Integer(), nullable=False),
        sa.Column("commit_id", sa.Integer(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("html_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("head_branch", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "merged", "closed", "failed", name="github_pull_request_status"),
            nullable=False,
        ),
        sa.Column(
            "ci_status",
            sa.Enum(
                "pending",
                "success",
                "failure",
                "error",
                "unknown",
                "unavailable",
                name="github_ci_status",
            ),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("ci_detail_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["commit_id"], ["commits.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("change_set_id", name="uq_pull_requests_change_set_id"),
    )


def downgrade() -> None:
    op.drop_table("pull_requests")
    op.drop_table("commits")
    sa.Enum(name="github_ci_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="github_pull_request_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="github_commit_status").drop(op.get_bind(), checkfirst=True)
    op.drop_constraint(
        "uq_platform_connections_project_platform",
        "platform_connections",
        type_="unique",
    )
    op.alter_column(
        "platform_connections",
        "website_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
