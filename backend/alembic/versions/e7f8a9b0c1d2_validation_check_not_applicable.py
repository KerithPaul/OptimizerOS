"""Add not_applicable to validation_check_status

Revision ID: e7f8a9b0c1d2
Revises: d1e2f3a4b5c6
Create Date: 2026-09-17

A missing package.json script is not a failed or skipped check — it does
not apply to that repository and must not make a validation run PARTIAL.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE validation_results MODIFY COLUMN status "
        "ENUM('passed', 'failed', 'skipped', 'not_applicable') NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE validation_results SET status = 'skipped' WHERE status = 'not_applicable'"
    )
    op.execute(
        "ALTER TABLE validation_results MODIFY COLUMN status "
        "ENUM('passed', 'failed', 'skipped') NOT NULL"
    )
