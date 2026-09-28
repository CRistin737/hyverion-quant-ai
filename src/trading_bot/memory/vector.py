"""Replaceable semantic index for strategic memory.

PostgreSQL uses pgvector (HNSW, cosine). SQLite has no vector index: the null
backend reports ``available = False`` so retrieval degrades explicitly to
metadata filters instead of pretending to search semantically.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from trading_bot.db.database import Database
from trading_bot.memory.embeddings import EMBEDDING_DIMENSIONS

MAX_NEIGHBOURS = 50

# Shared by migration 0005 and ``PgVectorBackend.ensure_schema``.
PG_EMBEDDING_DDL: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""
    CREATE TABLE IF NOT EXISTS memory_embeddings (
        memory_id VARCHAR(64) PRIMARY KEY REFERENCES strategic_memories(id),
        embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL,
        embedding_model VARCHAR(80) NOT NULL,
        embedding_version VARCHAR(80) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_memory_embeddings_hnsw
    ON memory_embeddings USING hnsw (embedding vector_cosine_ops)
    """,
)


def apply_pg_embedding_schema(connection: Connection) -> None:
    for statement in PG_EMBEDDING_DDL:
        connection.execute(text(statement))


class NullVectorBackend:
    """Explicitly unavailable semantic index (SQLite / development)."""

    available = False

    async def upsert(self, memory_id: str, embedding: Sequence[float], *, model: str) -> None:
        return None

    async def nearest(
        self, embedding: Sequence[float], *, limit: int
    ) -> list[tuple[str, float]]:
        return []

    async def similarities(
        self, embedding: Sequence[float], memory_ids: Sequence[str]
    ) -> dict[str, float]:
        return {}

    async def indexed(self, memory_ids: Sequence[str], *, model: str) -> dict[str, datetime]:
        return {}


class PgVectorBackend:
    available = True

    def __init__(self, database: Database, *, embedding_version: str = "1") -> None:
        if not database.url.startswith("postgresql"):
            raise ValueError("PgVectorBackend requires a PostgreSQL database")
        self._database = database
        self._version = embedding_version

    async def ensure_schema(self) -> None:
        async with self._database.engine.begin() as connection:
            await connection.run_sync(apply_pg_embedding_schema)

    async def upsert(self, memory_id: str, embedding: Sequence[float], *, model: str) -> None:
        literal = _vector_literal(embedding)
        async with self._database.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO memory_embeddings
                        (memory_id, embedding, embedding_model, embedding_version)
                    VALUES (:memory_id, CAST(:embedding AS vector), :model, :version)
                    ON CONFLICT (memory_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model,
                        embedding_version = EXCLUDED.embedding_version,
                        created_at = now()
                    """
                ),
                {
                    "memory_id": memory_id,
                    "embedding": literal,
                    "model": model,
                    "version": self._version,
                },
            )

    async def nearest(
        self, embedding: Sequence[float], *, limit: int
    ) -> list[tuple[str, float]]:
        """Return ``(memory_id, cosine_similarity)`` pairs, most similar first."""

        if not 1 <= limit <= MAX_NEIGHBOURS:
            raise ValueError(f"limit must be between 1 and {MAX_NEIGHBOURS}")
        literal = _vector_literal(embedding)
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT memory_id, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM memory_embeddings
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {"embedding": literal, "limit": limit},
            )
            return [(str(row[0]), float(row[1])) for row in result.all()]

    async def similarities(
        self, embedding: Sequence[float], memory_ids: Sequence[str]
    ) -> dict[str, float]:
        """Cosine similarity for exactly these memories (missing ones are unindexed)."""

        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return {}
        if len(ids) > MAX_NEIGHBOURS:
            raise ValueError(f"at most {MAX_NEIGHBOURS} memory ids per call")
        literal = _vector_literal(embedding)
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT memory_id, 1 - (embedding <=> CAST(:embedding AS vector))
                    FROM memory_embeddings
                    WHERE memory_id = ANY(:ids)
                    """
                ),
                {"embedding": literal, "ids": ids},
            )
            return {str(row[0]): float(row[1]) for row in result.all()}

    async def indexed(self, memory_ids: Sequence[str], *, model: str) -> dict[str, datetime]:
        """When each memory was last embedded with ``model`` and the current version.

        Vectors from another model or embedding version count as missing, so a model
        upgrade re-embeds everything on the next run.
        """

        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return {}
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT memory_id, created_at FROM memory_embeddings "
                    "WHERE memory_id = ANY(:ids) AND embedding_model = :model "
                    "AND embedding_version = :version"
                ),
                {"ids": ids, "model": model, "version": self._version},
            )
            return {
                str(row[0]): (row[1] if row[1].tzinfo else row[1].replace(tzinfo=UTC))
                for row in result.all()
            }


def _vector_literal(embedding: Sequence[float]) -> str:
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"embedding must have {EMBEDDING_DIMENSIONS} dimensions")
    values = [float(value) for value in embedding]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("embedding values must be finite")
    return "[" + ",".join(repr(value) for value in values) + "]"
