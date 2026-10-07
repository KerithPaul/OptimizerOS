"""Fix jobs.workspaces FK cascade

Revision ID: a1f0c9d8e7b6
Revises: f8a9b0c1d2e3
Create Date: 2026-09-23

`jobs` and `workspaces` were the only two tables whose project_id foreign
keys lacked ON DELETE CASCADE, which made DELETE /projects/{id} fail with
a foreign-key violation for any project that had jobs or a workspace row.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1f0c9d8e7b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("jobs_ibfk_1", type_="foreignkey")
        batch.create_foreign_key(
            "jobs_project_id_fkey", "projects", ["project_id"], ["id"], ondelete="CASCADE"
        )
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_constraint("workspaces_ibfk_1", type_="foreignkey")
        batch.create_foreign_key(
            "workspaces_project_id_fkey", "projects", ["project_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("jobs_project_id_fkey", type_="foreignkey")
        batch.create_foreign_key("jobs_ibfk_1", "projects", ["project_id"], ["id"])
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_constraint("workspaces_project_id_fkey", type_="foreignkey")
        batch.create_foreign_key("workspaces_ibfk_1", "projects", ["project_id"], ["id"])
