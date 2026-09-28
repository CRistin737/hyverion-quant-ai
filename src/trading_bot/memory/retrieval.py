"""Context-aware hybrid retrieval of strategic memory.

Metadata filtering always runs first and is time-aware (``as_of``) and
status-aware (ACTIVE only), so the semantic index can only *re-rank* knowledge
that already existed and was valid at that moment — never add future knowledge.

Live trading uses the current state of each memory. Research replays use
``point_in_time=True``: status, reliability and usage are rebuilt from snapshots
as they were at ``as_of``, so outcomes learned later cannot leak into the past.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_bot.memory.models import StrategicMemory
from trading_bot.memory.ports import EmbeddingProvider, MemoryRepository, VectorBackend

CANDIDATE_POOL = 50


@dataclass(frozen=True, slots=True)
class RetrievalWeights:
    """Configurable scoring basis (see the memory contract)."""

    global_regime_factor: float = 0.8
    mismatched_regime_factor: float = 0.5
    recency_half_life_days: float = 90.0
    recency_floor: float = 0.5
    importance_floor: float = 0.5
    # Used in semantic mode for memories that have no embedding yet.
    unindexed_similarity: float = 0.25


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    memory: StrategicMemory
    score: float
    semantic_similarity: float | None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    items: tuple[RetrievedMemory, ...]
    semantic: bool


class MemoryRetrievalEngine:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        vector: VectorBackend,
        embedder: EmbeddingProvider | None = None,
        weights: RetrievalWeights | None = None,
        point_in_time: bool = False,
    ) -> None:
        self._repository = repository
        self._vector = vector
        self._embedder = embedder
        self._weights = weights or RetrievalWeights()
        self._point_in_time = point_in_time

    async def retrieve(
        self,
        query: str,
        *,
        as_of: datetime,
        symbol: str | None = None,
        strategy: str | None = None,
        market_regime: str | None = None,
        limit: int = 5,
    ) -> RetrievalResult:
        if not 1 <= limit <= CANDIDATE_POOL:
            raise ValueError(f"limit must be between 1 and {CANDIDATE_POOL}")
        search = (
            self._repository.search_strategic_as_of
            if self._point_in_time
            else self._repository.search_strategic
        )
        candidates = await search(
            as_of=as_of,
            symbol=symbol,
            strategy=strategy,
            market_regime=market_regime,
            limit=CANDIDATE_POOL,
        )
        if not candidates:
            return RetrievalResult(items=(), semantic=self._semantic_ready)
        similarities: dict[str, float] = {}
        semantic = self._semantic_ready and bool(query.strip())
        if semantic:
            assert self._embedder is not None
            vector = (await self._embedder.embed([query]))[0]
            similarities = await self._vector.similarities(
                vector, [memory.id for memory in candidates]
            )
        scored = [
            RetrievedMemory(
                memory=memory,
                score=self._score(
                    memory,
                    as_of=as_of,
                    regime=market_regime,
                    similarity=similarities.get(memory.id),
                    semantic=semantic,
                ),
                semantic_similarity=similarities.get(memory.id) if semantic else None,
            )
            for memory in candidates
        ]
        scored.sort(key=lambda item: (-item.score, item.memory.id))
        return RetrievalResult(items=tuple(scored[:limit]), semantic=semantic)

    @property
    def _semantic_ready(self) -> bool:
        return self._embedder is not None and self._vector.available

    def _score(
        self,
        memory: StrategicMemory,
        *,
        as_of: datetime,
        regime: str | None,
        similarity: float | None,
        semantic: bool,
    ) -> float:
        weights = self._weights
        if not semantic:
            relevance = 1.0
        elif similarity is None:
            relevance = weights.unindexed_similarity
        else:
            relevance = max(0.0, min(1.0, similarity))
        if regime is None or memory.market_regime == regime:
            regime_factor = 1.0
        elif memory.market_regime is None:
            regime_factor = weights.global_regime_factor
        else:
            regime_factor = weights.mismatched_regime_factor
        # Recency follows evidence (last validation), not bookkeeping updates.
        reference = memory.last_validated_at or memory.created_at
        age_days = max(0.0, (as_of - reference).total_seconds() / 86400)
        recency = max(
            weights.recency_floor, math.pow(0.5, age_days / weights.recency_half_life_days)
        )
        importance = weights.importance_floor + (1 - weights.importance_floor) * float(
            memory.importance
        )
        return round(
            relevance * float(memory.reliability) * regime_factor * recency * importance, 6
        )


def unit_score(value: float) -> Decimal:
    """Clamp a retrieval score into the persisted ``0..1`` scale."""

    return Decimal(str(max(0.0, min(1.0, value)))).quantize(Decimal("0.00001"))
