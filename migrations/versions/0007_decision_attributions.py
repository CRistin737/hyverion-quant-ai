"""Add the decision attribution audit table used by meta-memory.

Revision ID: 0007
Revises: 0006

Additive only. ``0001`` builds fresh databases from the live metadata, so the
table may already exist; creation is therefore ``checkfirst``.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import decision_attributions

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    decision_attributions.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    decision_attributions.drop(bind=op.get_bind(), checkfirst=True)
