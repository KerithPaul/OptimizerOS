"""users: per-member project creation limit

Revision ID: d7e2f8a4c1b9
Revises: c5a9d1e4f7b2
Create Date: 2026-09-25

Adds `users.project_limit` (nullable int). NULL = unlimited. Admins are
always unlimited regardless of the stored value; the limit is enforced only
for role == 'member' when creating a project.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e2f8a4c1b9"
down_revision: Union[str, Sequence[str], None] = "c5a9d1e4f7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("project_limit", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_users_project_limit_values",
        "users",
        "project_limit IS NULL OR (project_limit >= 1 AND project_limit <= 100)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_project_limit_values", "users", type_="check")
    op.drop_column("users", "project_limit")
