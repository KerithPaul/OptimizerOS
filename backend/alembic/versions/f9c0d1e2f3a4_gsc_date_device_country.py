"""Add date, device, and country to search_console_dimension

Revision ID: f9c0d1e2f3a4
Revises: e8a1b2c3d4e5
Create Date: 2026-10-01

Site health reports need daily Search Analytics plus device and country
breakdowns. Page/query/page_query stay on the 28-day window.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "e8a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UP = (
    "ALTER TABLE search_console_data MODIFY COLUMN dimension "
    "ENUM('page','query','page_query','date','device','country') NOT NULL"
)
_DOWN = (
    "ALTER TABLE search_console_data MODIFY COLUMN dimension "
    "ENUM('page','query','page_query') NOT NULL"
)


def upgrade() -> None:
    op.execute(_UP)


def downgrade() -> None:
    op.execute(
        "DELETE FROM search_console_data WHERE dimension IN ('date','device','country')"
    )
    op.execute(_DOWN)
