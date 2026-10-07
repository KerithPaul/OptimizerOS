"""WordPress CMS agent type, snapshot reason, and change platform

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-09-18

Phase 10: WordPress mutations reuse snapshots / change_transactions.
Extend native enums; no new tables. Credentials stay on platform_connections.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_runs MODIFY COLUMN agent_type "
        "ENUM('research','seo','aeo','geo','code','reviewer','cms') NOT NULL"
    )
    op.execute(
        "ALTER TABLE snapshots MODIFY COLUMN reason "
        "ENUM('before_code_change','before_cms_change') NOT NULL"
    )
    op.execute(
        "ALTER TABLE change_transactions MODIFY COLUMN platform "
        "ENUM('git','wordpress') NOT NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_runs WHERE agent_type = 'cms'")
    op.execute("DELETE FROM snapshots WHERE reason = 'before_cms_change'")
    op.execute("DELETE FROM change_transactions WHERE platform = 'wordpress'")
    op.execute(
        "ALTER TABLE agent_runs MODIFY COLUMN agent_type "
        "ENUM('research','seo','aeo','geo','code','reviewer') NOT NULL"
    )
    op.execute(
        "ALTER TABLE snapshots MODIFY COLUMN reason "
        "ENUM('before_code_change') NOT NULL"
    )
    op.execute(
        "ALTER TABLE change_transactions MODIFY COLUMN platform "
        "ENUM('git') NOT NULL"
    )
