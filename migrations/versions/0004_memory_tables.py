"""Add the portable historical/strategic memory tables.

Revision ID: 0004
Revises: 0003

Additive only and idempotent on fresh databases (``0001`` builds from live
metadata). PostgreSQL-only embeddings arrive in a later, dialect-guarded revision.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import MEMORY_TABLES

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in MEMORY_TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(MEMORY_TABLES):
        table.drop(bind=bind, checkfirst=True)
