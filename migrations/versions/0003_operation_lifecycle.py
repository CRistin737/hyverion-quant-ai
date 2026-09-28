"""Add the operation lifecycle projection and its append-only transition log.

Revision ID: 0003
Revises: 0002

Additive only. ``0001`` builds the schema from the live metadata, so a fresh
database may already contain these tables; creation is therefore ``checkfirst``.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import operation_events, operations

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    operations.create(bind=bind, checkfirst=True)
    operation_events.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    operation_events.drop(bind=bind, checkfirst=True)
    operations.drop(bind=bind, checkfirst=True)
