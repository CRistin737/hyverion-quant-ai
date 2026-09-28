"""Add equity_snapshots: broker equity history for period reports.

Revision ID: 0011
Revises: 0010

Additive only; ``checkfirst`` because ``0001`` builds fresh databases from the
live metadata.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import equity_snapshots

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    equity_snapshots.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    equity_snapshots.drop(bind=op.get_bind(), checkfirst=True)
