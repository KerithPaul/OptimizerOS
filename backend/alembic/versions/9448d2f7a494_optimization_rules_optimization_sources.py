"""optimization_sources, optimization_rules

Revision ID: 9448d2f7a494
Revises: d4e5f6a7b8c9
Create Date: 2026-09-08 17:25:57.178154

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9448d2f7a494'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_AUTHORITY_LEVELS = (
    "official_standard",
    "official_vendor_docs",
    "peer_reviewed_research",
    "reputable_practitioner",
    "community_unverified",
)


def upgrade() -> None:
    op.create_table(
        "optimization_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "authority",
            sa.Enum(*_AUTHORITY_LEVELS, name="authority_level"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "optimization_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "technical_seo",
                "content_seo",
                "aeo",
                "geo",
                name="rule_category",
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "authority",
            sa.Enum(*_AUTHORITY_LEVELS, name="authority_level"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "low",
                "medium",
                "high",
                "critical",
                name="rule_severity",
            ),
            nullable=False,
        ),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Enum(
                "low",
                "medium",
                "high",
                name="rule_confidence",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["optimization_sources.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id", "version", name="uq_optimization_rules_rule_id_version"
        ),
    )


def downgrade() -> None:
    op.drop_table("optimization_rules")
    op.drop_table("optimization_sources")
    sa.Enum(name="rule_confidence").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="rule_severity").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="rule_category").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="authority_level").drop(op.get_bind(), checkfirst=True)
