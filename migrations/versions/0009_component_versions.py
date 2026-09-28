"""Add versions of agents and strategy parameters (review-and-apply workflow).

Revision ID: 0009
Revises: 0008

Additive only. ``0001`` builds fresh databases from the live metadata, so the
table may already exist; creation is therefore ``checkfirst``.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import component_versions

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    component_versions.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    component_versions.drop(bind=op.get_bind(), checkfirst=True)
