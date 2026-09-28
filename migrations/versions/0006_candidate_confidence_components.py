"""Persist deterministic confidence components on memory candidates.

Revision ID: 0006
Revises: 0005

Additive and idempotent: ``0001`` builds fresh databases from live metadata,
which already declares the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("memory_candidates")
    }
    if "confidence_components" not in columns:
        op.add_column(
            "memory_candidates", sa.Column("confidence_components", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("memory_candidates", "confidence_components")
