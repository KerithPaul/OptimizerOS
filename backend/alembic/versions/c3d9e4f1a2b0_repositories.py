"""repositories

Revision ID: c3d9e4f1a2b0
Revises: b2f8c1a09e47
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d9e4f1a2b0"
down_revision: Union[str, Sequence[str], None] = "b2f8c1a09e47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("default_branch", sa.String(length=255), server_default="main", nullable=False),
        sa.Column("cloned_commit_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "clone_status",
            sa.Enum(
                "pending",
                "cloning",
                "cloned",
                "clone_failed",
                name="clone_status",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_indexed_commit", sa.String(length=64), nullable=True),
        sa.Column("architecture_profile", sa.JSON(), nullable=True),
        sa.Column("clone_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_repositories_project_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("repositories")
    sa.Enum(name="clone_status").drop(op.get_bind(), checkfirst=True)
