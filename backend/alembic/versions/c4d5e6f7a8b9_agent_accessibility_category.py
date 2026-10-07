"""Add agent_accessibility to rule and validation enums.

Revision ID: c4d5e6f7a8b9
Revises: b0c1d2e3f4a5
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RULE_CATEGORIES = (
    "'technical_seo', 'content_seo', 'aeo', 'geo', 'agent_accessibility'"
)
_RULE_CATEGORIES_PREVIOUS = "'technical_seo', 'content_seo', 'aeo', 'geo'"
_CHECK_TYPES = (
    "'scope', 'content', 'lint', 'typecheck', 'build', 'unit_test', "
    "'integration_test', 'browser', 'seo', 'aeo', 'geo', "
    "'agent_accessibility', 'regression'"
)
_CHECK_TYPES_PREVIOUS = (
    "'scope', 'content', 'lint', 'typecheck', 'build', 'unit_test', "
    "'integration_test', 'browser', 'seo', 'aeo', 'geo', 'regression'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE optimization_rules MODIFY COLUMN category "
        f"ENUM({_RULE_CATEGORIES}) NOT NULL"
    )
    op.execute(
        "ALTER TABLE findings MODIFY COLUMN category "
        f"ENUM({_RULE_CATEGORIES}) NOT NULL"
    )
    op.execute(
        "ALTER TABLE validation_results MODIFY COLUMN check_type "
        f"ENUM({_CHECK_TYPES}) NOT NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM findings WHERE category = 'agent_accessibility'")
    op.execute("DELETE FROM optimization_rules WHERE category = 'agent_accessibility'")
    op.execute(
        "DELETE FROM validation_results WHERE check_type = 'agent_accessibility'"
    )
    op.execute(
        "ALTER TABLE optimization_rules MODIFY COLUMN category "
        f"ENUM({_RULE_CATEGORIES_PREVIOUS}) NOT NULL"
    )
    op.execute(
        "ALTER TABLE findings MODIFY COLUMN category "
        f"ENUM({_RULE_CATEGORIES_PREVIOUS}) NOT NULL"
    )
    op.execute(
        "ALTER TABLE validation_results MODIFY COLUMN check_type "
        f"ENUM({_CHECK_TYPES_PREVIOUS}) NOT NULL"
    )
