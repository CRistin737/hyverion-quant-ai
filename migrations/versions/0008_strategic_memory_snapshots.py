"""Add point-in-time snapshots of strategic memory for leakage-free replays.

Revision ID: 0008
Revises: 0007

Additive only. ``0001`` builds fresh databases from the live metadata, so the
table may already exist; creation is therefore ``checkfirst``.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import strategic_memory_snapshots

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    strategic_memory_snapshots.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    strategic_memory_snapshots.drop(bind=op.get_bind(), checkfirst=True)
