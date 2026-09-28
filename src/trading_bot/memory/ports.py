"""Storage ports for the three memory layers.

Implementations are injected; agents never receive any of these objects.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol

from trading_bot.memory.models import (
    CandidateStatus,
    EvidenceLink,
    KnowledgeStatus,
    MemoryCandidate,
    MemoryConflict,
    MemoryOutcome,
    MemoryUsage,
    StrategicMemory,
    StrategicMemoryVersion,
)


class WorkingMemoryBackend(Protocol):
    """Disposable low-latency state. Never a source of truth; always rebuildable."""

    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def set(
        self, key: str, value: dict[str, Any], *, ttl: timedelta | None = None
    ) -> None: ...

    async def delete(self, key: str) -> None: ...


class MemoryRepository(Protocol):
    """Historical/strategic memory persistence (authoritative)."""

    async def add_candidate(
        self, candidate: MemoryCandidate, evidence: Sequence[EvidenceLink] = ()
    ) -> None: ...

    async def get_candidate(self, candidate_id: str) -> MemoryCandidate | None: ...

    async def set_candidate_status(self, candidate_id: str, status: CandidateStatus) -> None: ...

    async def evidence(self, candidate_id: str) -> list[EvidenceLink]: ...

    async def create_strategic(
        self, memory: StrategicMemory, *, content: str, created_by: str, change_reason: str
    ) -> StrategicMemoryVersion: ...

    async def revise_strategic(
        self, memory_id: str, *, content: str, created_by: str, change_reason: str, now: datetime
    ) -> StrategicMemoryVersion: ...

    async def get_strategic(self, memory_id: str) -> StrategicMemory | None: ...

    async def versions(self, memory_id: str) -> list[StrategicMemoryVersion]: ...

    async def search_strategic(
        self,
        *,
        as_of: datetime,
        symbol: str | None = None,
        strategy: str | None = None,
        market_regime: str | None = None,
        statuses: frozenset[KnowledgeStatus] = frozenset({KnowledgeStatus.ACTIVE}),
        limit: int = 20,
    ) -> list[StrategicMemory]: ...

    async def search_strategic_as_of(
        self,
        *,
        as_of: datetime,
        symbol: str | None = None,
        strategy: str | None = None,
        market_regime: str | None = None,
        limit: int = 20,
    ) -> list[StrategicMemory]: ...

    async def set_strategic_status(
        self, memory_id: str, status: KnowledgeStatus, *, now: datetime
    ) -> None: ...

    async def record_usage(self, usage: MemoryUsage) -> None: ...

    async def record_outcome(self, outcome: MemoryOutcome) -> None: ...

    async def record_conflict(self, conflict: MemoryConflict) -> None: ...

    async def relink_usage(self, old_decision_id: str, new_decision_id: str) -> None: ...


class VaultRepository(Protocol):
    """Human-readable Markdown export of strategic memory (derived, not authoritative)."""

    async def write(
        self,
        memory: StrategicMemory,
        version: StrategicMemoryVersion,
        *,
        conflicts: Sequence[str] = (),
    ) -> str: ...


class VectorBackend(Protocol):
    """Replaceable semantic index. A null backend is explicit on SQLite."""

    available: bool

    async def upsert(self, memory_id: str, embedding: Sequence[float], *, model: str) -> None: ...

    async def nearest(
        self, embedding: Sequence[float], *, limit: int
    ) -> list[tuple[str, float]]: ...

    async def similarities(
        self, embedding: Sequence[float], memory_ids: Sequence[str]
    ) -> dict[str, float]: ...

    async def indexed(
        self, memory_ids: Sequence[str], *, model: str
    ) -> dict[str, datetime]: ...


class EmbeddingProvider(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
