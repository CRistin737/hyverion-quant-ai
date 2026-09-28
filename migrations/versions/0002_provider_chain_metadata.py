"""Persist provider attempts for auditable subscription failover.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds the schema from the live metadata, which already declares this
    # column on a fresh database; only older databases need the ALTER.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("model_usage")}
    if "attempted_providers" in columns:
        return
    op.add_column(
        "model_usage",
        sa.Column("attempted_providers", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_usage", "attempted_providers")
