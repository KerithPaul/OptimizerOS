"""merge heads

Revision ID: de69f2426879
Revises: a3c9e1f7b2d4, c4d5e6f7a8b9
Create Date: 2026-09-28 10:44:31.439226

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de69f2426879'
down_revision: Union[str, Sequence[str], None] = ('a3c9e1f7b2d4', 'c4d5e6f7a8b9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
