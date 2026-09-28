"""Add the pgvector semantic index for strategic memory (PostgreSQL only).

Revision ID: 0005
Revises: 0004

On SQLite this revision is a no-op: the application uses the explicit null
vector backend there. Requires the pgvector extension to be installable.
"""
from __future__ import annotations

from alembic import op

from trading_bot.memory.vector import apply_pg_embedding_schema

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    apply_pg_embedding_schema(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS memory_embeddings")
