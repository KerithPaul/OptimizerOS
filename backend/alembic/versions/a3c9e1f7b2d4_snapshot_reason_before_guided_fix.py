"""Add before_guided_fix to snapshot_reason

Revision ID: a3c9e1f7b2d4
Revises: d7e2f8a4c1b9
Create Date: 2026-09-25

The guided fix workflow captures one file's exact pre-modification
content before the user-triggered fix is applied (STEP 3). It reuses the
`snapshots` table with a new reason value — a row-level snapshot of only
the touched files (`files_json`) instead of a whole-workspace copy.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a3c9e1f7b2d4"
down_revision: Union[str, Sequence[str], None] = "d7e2f8a4c1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE snapshots MODIFY COLUMN reason "
        "ENUM('before_code_change', 'before_cms_change', 'before_guided_fix') NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE snapshots SET reason = 'before_code_change' WHERE reason = 'before_guided_fix'"
    )
    op.execute(
        "ALTER TABLE snapshots MODIFY COLUMN reason "
        "ENUM('before_code_change', 'before_cms_change') NOT NULL"
    )
