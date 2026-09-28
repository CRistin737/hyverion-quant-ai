"""Memory maintenance: gateway construction, semantic re-indexing and working-memory rebuild.

Batch operations only (never per tick). The database stays authoritative; the semantic
index and working memory are disposable projections rebuilt from it.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.state import TradingStateRepository
from trading_bot.memory.embeddings import EmbeddingUnavailable, LocalEmbeddingProvider
from trading_bot.memory.gateway import MemoryGateway
from trading_bot.memory.models import KnowledgeStatus, StrategicMemory
from trading_bot.memory.ports import EmbeddingProvider, VectorBackend, WorkingMemoryBackend
from trading_bot.memory.repository import SqlMemoryRepository
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vector import NullVectorBackend, PgVectorBackend

# --- indexer ---------------------------------------------------------------

BATCH_SIZE = 32
_INDEXED_STATUSES = frozenset({KnowledgeStatus.ACTIVE, KnowledgeStatus.NEEDS_REVALIDATION})


@dataclass(frozen=True, slots=True)
class IndexReport:
    available: bool
    indexed: int = 0
    up_to_date: int = 0


class MemoryIndexer:
    def __init__(
        self,
        *,
        repository: SqlMemoryRepository,
        vector: VectorBackend,
        embedder: EmbeddingProvider | None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._repository = repository
        self._vector = vector
        self._embedder = embedder
        self._batch_size = batch_size

    async def run(self, *, force: bool = False) -> IndexReport:
        if not self._vector.available or self._embedder is None:
            return IndexReport(available=False)
        memories = await self._repository.list_strategic(_INDEXED_STATUSES, limit=500)
        indexed_at = (
            {}
            if force
            else await self._vector.indexed(
                [m.id for m in memories], model=self._embedder.model
            )
        )
        stale = [
            memory
            for memory in memories
            if memory.id not in indexed_at or indexed_at[memory.id] < memory.updated_at
        ]
        for start in range(0, len(stale), self._batch_size):
            batch = stale[start : start + self._batch_size]
            vectors = await self._embedder.embed([embedding_text(memory) for memory in batch])
            for memory, vector in zip(batch, vectors, strict=True):
                await self._vector.upsert(memory.id, vector, model=self._embedder.model)
        return IndexReport(
            available=True, indexed=len(stale), up_to_date=len(memories) - len(stale)
        )


def embedding_text(memory: StrategicMemory) -> str:
    """What the index represents: the claim and its short summary, never raw evidence."""

    parts = [memory.title, memory.summary]
    if memory.symbol:
        parts.append(memory.symbol)
    if memory.market_regime:
        parts.append(memory.market_regime.lower().replace("_", " "))
    return "\n".join(parts)


# --- rebuild ---------------------------------------------------------------

# Keys expire so a stale projection cannot outlive a crashed rebuild loop.
DEFAULT_TTL = timedelta(minutes=15)

_POSITION_FIELDS = (
    "position_id",
    "operation_id",
    "asset",
    "side",
    "entry_price",
    "current_price",
    "quantity",
    "stop_price",
    "target_price",
    "unrealized_pnl",
    "protective_stop_active",
    "status",
)


class WorkingMemoryRebuilder:
    def __init__(
        self,
        *,
        backend: WorkingMemoryBackend,
        state: TradingStateRepository,
        lifecycle: OperationLifecycleRepository,
        clock: Clock,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._backend = backend
        self._state = state
        self._lifecycle = lifecycle
        self._clock = clock
        self._ttl = ttl

    async def rebuild(self) -> dict[str, Any]:
        positions = await self._state.open_positions()
        for position in positions:
            await self._backend.set(
                f"position:{position['position_id']}",
                {key: _json_safe(position.get(key)) for key in _POSITION_FIELDS},
                ttl=self._ttl,
            )
        active = await self._lifecycle.active()
        summary = {
            "rebuilt_at": self._clock.now().isoformat(),
            "open_position_ids": sorted(str(row["position_id"]) for row in positions),
            "active_operations": [
                {"id": str(row["id"]), "state": str(row["state"]), "asset": str(row["asset"])}
                for row in active
            ],
            "unresolved_operations": await self._lifecycle.count_unresolved(),
        }
        await self._backend.set("session:current", summary, ttl=self._ttl)
        return summary


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, bool | int | str):
        return value
    return str(value)


# --- factory ---------------------------------------------------------------

logger = structlog.get_logger(__name__)


class MemoryConfigurationError(RuntimeError):
    """The memory stack required for this environment is not available."""


# The model is static: hash-verify and load it once per process, off the event loop.
_PROVIDERS: dict[Path, LocalEmbeddingProvider] = {}
# A thread lock: the CLI creates a fresh event loop per cycle.
_PROVIDER_LOCK = threading.Lock()


def _load_provider(model_dir: Path) -> LocalEmbeddingProvider:
    key = model_dir.resolve()
    with _PROVIDER_LOCK:
        provider = _PROVIDERS.get(key)
        if provider is None:
            provider = LocalEmbeddingProvider(key)
            _PROVIDERS[key] = provider
        return provider


async def _embedding_provider(model_dir: Path) -> LocalEmbeddingProvider:
    return await asyncio.to_thread(_load_provider, model_dir)


async def semantic_stack(
    settings: Settings, database: Database
) -> tuple[VectorBackend, EmbeddingProvider | None]:
    """pgvector + the verified local model on PostgreSQL; an explicit null index otherwise."""

    if not database.url.startswith("postgresql"):
        return NullVectorBackend(), None
    try:
        embedder = await _embedding_provider(settings.public.memory.embedding_model_dir)
    except EmbeddingUnavailable as exc:
        logger.warning("memory_semantic_disabled", reason=str(exc))
        embedder = None
    return PgVectorBackend(database), embedder


async def build_memory_gateway(settings: Settings, database: Database) -> MemoryGateway:
    repository = SqlMemoryRepository(database)
    vector, embedder = await semantic_stack(settings, database)
    production = settings.public.app.environment == "production"
    if production and (embedder is None or not vector.available):
        raise MemoryConfigurationError(
            "production requires PostgreSQL + pgvector and the verified local embedding model"
        )
    retrieval = MemoryRetrievalEngine(repository=repository, vector=vector, embedder=embedder)
    return MemoryGateway(repository=repository, retrieval=retrieval)
