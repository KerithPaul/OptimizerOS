"""users: name, role, status for admin user management

Revision ID: c5a9d1e4f7b2
Revises: a1f0c9d8e7b6
Create Date: 2026-09-24

Adds the admin-facing profile columns to `users`. Existing rows backfill
to ('', 'member', 'active'); the seed promotion to admin happens in code
(security.promote_seed_admin), not here, so the migration stays
environment-independent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5a9d1e4f7b2"
down_revision: Union[str, Sequence[str], None] = "a1f0c9d8e7b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default="member",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "ck_users_role_values",
        "users",
        "role IN ('admin', 'member')",
    )
    op.create_check_constraint(
        "ck_users_status_values",
        "users",
        "status IN ('active', 'suspended')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_status_values", "users", type_="check")
    op.drop_constraint("ck_users_role_values", "users", type_="check")
    op.drop_column("users", "status")
    op.drop_column("users", "role")
    op.drop_column("users", "name")
