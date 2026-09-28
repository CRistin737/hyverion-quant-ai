"""Add QQQ intelligence tables: macro calendar, index holdings, sensor snapshots.

Revision ID: 0010
Revises: 0009

Additive only. ``0001`` builds fresh databases from the live metadata, so the
tables may already exist; creation is therefore ``checkfirst``.
"""
from __future__ import annotations

from alembic import op

from trading_bot.db.models import (
    breadth_snapshots,
    component_snapshots,
    earnings_events,
    index_holdings,
    macro_events,
    news_clusters,
    options_snapshots,
    rates_snapshots,
    sec_filings,
)

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_TABLES = (
    macro_events,
    index_holdings,
    breadth_snapshots,
    component_snapshots,
    sec_filings,
    earnings_events,
    news_clusters,
    options_snapshots,
    rates_snapshots,
)


def upgrade() -> None:
    for table in _TABLES:
        table.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for table in reversed(_TABLES):
        table.drop(bind=op.get_bind(), checkfirst=True)
