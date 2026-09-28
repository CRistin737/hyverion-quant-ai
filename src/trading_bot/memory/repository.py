"""SQLAlchemy Core implementation of ``MemoryRepository`` (SQLite and PostgreSQL).

Strategic memory is versioned and never deleted: retirement is a status change,
and every content change appends a version with a SHA-256 content hash.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from trading_bot.db.database import Database
from trading_bot.db.models import (
    memory_candidates,
    memory_conflicts,
    memory_evidence,
    memory_outcomes,
    memory_usage,
    strategic_memories,
    strategic_memory_snapshots,
    strategic_memory_versions,
)
from trading_bot.memory.models import (
    CandidateStatus,
    EvidenceLink,
    KnowledgeStatus,
    MemoryCandidate,
    MemoryConflict,
    MemoryOutcome,
    MemoryType,
    MemoryUsage,
    StrategicMemory,
    StrategicMemoryVersion,
)

MAX_SEARCH_LIMIT = 50


class SqlMemoryRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def add_candidate(
        self, candidate: MemoryCandidate, evidence: Sequence[EvidenceLink] = ()
    ) -> None:
        if any(link.memory_candidate_id != candidate.id for link in evidence):
            raise ValueError("evidence must reference the candidate being added")
        async with self._database.engine.begin() as connection:
            values = candidate.model_dump()
            values["source_ids"] = list(candidate.source_ids)
            values["confidence_components"] = _components_json(candidate.confidence_components)
            await connection.execute(insert(memory_candidates).values(**values))
            for link in evidence:
                await connection.execute(insert(memory_evidence).values(**link.model_dump()))

    async def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        row = await self._one(
            select(memory_candidates).where(memory_candidates.c.id == candidate_id)
        )
        return MemoryCandidate.model_validate(_aware(row)) if row is not None else None

    async def set_candidate_status(self, candidate_id: str, status: CandidateStatus) -> None:
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                update(memory_candidates)
                .where(memory_candidates.c.id == candidate_id)
                .values(status=status.value)
            )
            if result.rowcount == 0:
                raise LookupError(f"unknown memory candidate: {candidate_id}")

    async def update_candidate_scores(
        self,
        candidate_id: str,
        *,
        confidence: Decimal,
        importance: Decimal,
        novelty: Decimal,
        evidence_strength: Decimal,
        components: dict[str, Decimal],
        status: CandidateStatus,
    ) -> None:
        """Persist the curator's deterministic scores and decision in one statement."""

        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                update(memory_candidates)
                .where(memory_candidates.c.id == candidate_id)
                .values(
                    confidence=confidence,
                    importance=importance,
                    novelty=novelty,
                    evidence_strength=evidence_strength,
                    confidence_components=_components_json(components),
                    status=status.value,
                )
            )
            if result.rowcount == 0:
                raise LookupError(f"unknown memory candidate: {candidate_id}")

    async def list_candidates(
        self, statuses: frozenset[CandidateStatus], *, limit: int = 100
    ) -> list[MemoryCandidate]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = await self._all(
            select(memory_candidates)
            .where(memory_candidates.c.status.in_([status.value for status in statuses]))
            .order_by(memory_candidates.c.created_at, memory_candidates.c.id)
            .limit(limit)
        )
        return [MemoryCandidate.model_validate(_aware(row)) for row in rows]

    async def evidence(self, candidate_id: str) -> list[EvidenceLink]:
        rows = await self._all(
            select(memory_evidence)
            .where(memory_evidence.c.memory_candidate_id == candidate_id)
            .order_by(memory_evidence.c.created_at, memory_evidence.c.id)
        )
        return [EvidenceLink.model_validate(_aware(row)) for row in rows]

    async def create_strategic(
        self, memory: StrategicMemory, *, content: str, created_by: str, change_reason: str
    ) -> StrategicMemoryVersion:
        version = StrategicMemoryVersion(
            id=str(uuid4()),
            strategic_memory_id=memory.id,
            version=1,
            content=content,
            content_hash=_hash(content),
            previous_version=None,
            change_reason=change_reason,
            created_by=created_by,
            created_at=memory.created_at,
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(insert(strategic_memories).values(**memory.model_dump()))
            await connection.execute(
                insert(strategic_memory_versions).values(**version.model_dump())
            )
            await self._snapshot(connection, memory.id, memory.created_at)
        return version

    async def revise_strategic(
        self, memory_id: str, *, content: str, created_by: str, change_reason: str, now: datetime
    ) -> StrategicMemoryVersion:
        async with self._database.engine.begin() as connection:
            latest = await self._latest_version(connection, memory_id)
            if latest is None:
                raise LookupError(f"unknown strategic memory: {memory_id}")
            if latest.content_hash == _hash(content):
                # Identical content is not a new version; keep the history meaningful.
                return latest
            version = StrategicMemoryVersion(
                id=str(uuid4()),
                strategic_memory_id=memory_id,
                version=latest.version + 1,
                content=content,
                content_hash=_hash(content),
                previous_version=latest.version,
                change_reason=change_reason,
                created_by=created_by,
                created_at=now,
            )
            await connection.execute(
                insert(strategic_memory_versions).values(**version.model_dump())
            )
            await connection.execute(
                update(strategic_memories)
                .where(strategic_memories.c.id == memory_id)
                .values(updated_at=version.created_at)
            )
        return version

    async def get_strategic(self, memory_id: str) -> StrategicMemory | None:
        row = await self._one(
            select(strategic_memories).where(strategic_memories.c.id == memory_id)
        )
        return StrategicMemory.model_validate(_aware(row)) if row is not None else None

    async def versions(self, memory_id: str) -> list[StrategicMemoryVersion]:
        rows = await self._all(
            select(strategic_memory_versions)
            .where(strategic_memory_versions.c.strategic_memory_id == memory_id)
            .order_by(strategic_memory_versions.c.version)
        )
        return [StrategicMemoryVersion.model_validate(_aware(row)) for row in rows]

    async def search_strategic(
        self,
        *,
        as_of: datetime,
        symbol: str | None = None,
        strategy: str | None = None,
        market_regime: str | None = None,
        statuses: frozenset[KnowledgeStatus] = frozenset({KnowledgeStatus.ACTIVE}),
        limit: int = 20,
    ) -> list[StrategicMemory]:
        """Metadata search that never returns knowledge created after ``as_of``."""

        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        moment = _utc(as_of)
        table = strategic_memories
        statement = select(table).where(
            table.c.status.in_([status.value for status in statuses]),
            table.c.created_at <= moment,
            table.c.valid_from <= moment,
            or_(table.c.valid_until.is_(None), table.c.valid_until > moment),
        )
        # A filter matches the exact value or global (NULL) knowledge.
        for column, value in (
            (table.c.symbol, symbol),
            (table.c.strategy, strategy),
            (table.c.market_regime, market_regime),
        ):
            if value is not None:
                statement = statement.where(or_(column == value, column.is_(None)))
        statement = statement.order_by(
            table.c.reliability.desc(), table.c.importance.desc(), table.c.id
        ).limit(limit)
        rows = await self._all(statement)
        return [StrategicMemory.model_validate(_aware(row)) for row in rows]

    async def set_strategic_status(
        self, memory_id: str, status: KnowledgeStatus, *, now: datetime
    ) -> None:
        values: dict[str, Any] = {"status": status.value, "updated_at": _utc(now)}
        if status is KnowledgeStatus.RETIRED:
            values["valid_until"] = _utc(now)
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                update(strategic_memories)
                .where(strategic_memories.c.id == memory_id)
                .values(**values)
            )
            if result.rowcount == 0:
                raise LookupError(f"unknown strategic memory: {memory_id}")
            await self._snapshot(connection, memory_id, now)

    async def record_usage(self, usage: MemoryUsage) -> None:
        await self._insert(memory_usage, usage.model_dump())

    async def record_outcome(self, outcome: MemoryOutcome) -> None:
        values = outcome.model_dump(mode="json")
        values["created_at"] = outcome.created_at
        values["estimated_contribution"] = outcome.estimated_contribution
        await self._insert(memory_outcomes, values)

    async def record_conflict(self, conflict: MemoryConflict) -> None:
        if conflict.memory_a_id == conflict.memory_b_id:
            raise ValueError("a memory cannot conflict with itself")
        await self._insert(memory_conflicts, conflict.model_dump())

    async def relink_usage(self, old_decision_id: str, new_decision_id: str) -> None:
        async with self._database.engine.begin() as connection:
            await connection.execute(
                update(memory_usage)
                .where(memory_usage.c.decision_id == old_decision_id)
                .values(decision_id=new_decision_id)
            )

    async def usage_for_decision(self, decision_id: str) -> list[MemoryUsage]:
        rows = await self._all(
            select(memory_usage)
            .where(memory_usage.c.decision_id == decision_id)
            .order_by(memory_usage.c.created_at, memory_usage.c.id)
        )
        return [MemoryUsage.model_validate(_aware(row)) for row in rows]

    async def has_outcome(self, memory_id: str, trade_id: str) -> bool:
        row = await self._one(
            select(memory_outcomes.c.id).where(
                memory_outcomes.c.memory_id == memory_id,
                memory_outcomes.c.trade_id == trade_id,
            )
        )
        return row is not None

    async def conflict_exists(self, memory_a_id: str, memory_b_id: str) -> bool:
        pair = {memory_a_id, memory_b_id}
        row = await self._one(
            select(memory_conflicts.c.id).where(
                memory_conflicts.c.memory_a_id.in_(pair),
                memory_conflicts.c.memory_b_id.in_(pair),
            )
        )
        return row is not None

    async def list_strategic(
        self, statuses: frozenset[KnowledgeStatus], *, limit: int = 200
    ) -> list[StrategicMemory]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = await self._all(
            select(strategic_memories)
            .where(strategic_memories.c.status.in_([status.value for status in statuses]))
            .order_by(strategic_memories.c.created_at, strategic_memories.c.id)
            .limit(limit)
        )
        return [StrategicMemory.model_validate(_aware(row)) for row in rows]

    async def update_strategic_stats(
        self,
        memory_id: str,
        *,
        reliability: Decimal,
        successful_uses: int,
        failed_uses: int,
        status: KnowledgeStatus,
        now: datetime,
        last_validated_at: datetime | None = None,
        validation_count: int | None = None,
    ) -> None:
        """Deterministic lifecycle update; knowledge is never deleted."""

        values: dict[str, Any] = {
            "reliability": reliability,
            "successful_uses": successful_uses,
            "failed_uses": failed_uses,
            "status": status.value,
            "updated_at": _utc(now),
        }
        if status is KnowledgeStatus.RETIRED:
            values["valid_until"] = _utc(now)
        if last_validated_at is not None:
            values["last_validated_at"] = _utc(last_validated_at)
        if validation_count is not None:
            values["validation_count"] = validation_count
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                update(strategic_memories)
                .where(strategic_memories.c.id == memory_id)
                .values(**values)
            )
            if result.rowcount == 0:
                raise LookupError(f"unknown strategic memory: {memory_id}")
            await self._snapshot(connection, memory_id, now)

    async def count_versions(self, memory_id: str) -> int:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(func.count())
                .select_from(strategic_memory_versions)
                .where(strategic_memory_versions.c.strategic_memory_id == memory_id)
            )
            return int(result.scalar_one())

    # --- Operator views (dashboard and CLI) -------------------------------------------

    async def find_strategic(self, identifier: str) -> StrategicMemory | None:
        """Look a memory up by its internal id or its public ``KNOW-`` id."""

        row = await self._one(
            select(strategic_memories).where(
                or_(
                    strategic_memories.c.id == identifier,
                    strategic_memories.c.knowledge_id == identifier,
                )
            )
        )
        return StrategicMemory.model_validate(_aware(row)) if row is not None else None

    async def browse_strategic(
        self,
        *,
        text: str | None = None,
        symbol: str | None = None,
        strategy: str | None = None,
        market_regime: str | None = None,
        statuses: frozenset[KnowledgeStatus] = frozenset(KnowledgeStatus),
        min_reliability: Decimal | None = None,
        limit: int = 100,
    ) -> list[StrategicMemory]:
        """Exact-filter listing for operators; unlike retrieval it is not time-bounded."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        table = strategic_memories
        statement = select(table).where(
            table.c.status.in_([status.value for status in statuses])
        )
        for column, value in (
            (table.c.symbol, symbol),
            (table.c.strategy, strategy),
            (table.c.market_regime, market_regime),
        ):
            if value:
                statement = statement.where(column == value)
        if min_reliability is not None:
            statement = statement.where(table.c.reliability >= min_reliability)
        if text:
            pattern = "%" + _escape_like(text.strip()[:80]) + "%"
            statement = statement.where(
                or_(
                    table.c.title.ilike(pattern, escape="\\"),
                    table.c.summary.ilike(pattern, escape="\\"),
                    table.c.knowledge_id.ilike(pattern, escape="\\"),
                )
            )
        statement = statement.order_by(table.c.updated_at.desc(), table.c.id).limit(limit)
        return [StrategicMemory.model_validate(_aware(row)) for row in await self._all(statement)]

    async def set_strategic_category(
        self, memory_id: str, category: MemoryType, *, now: datetime
    ) -> None:
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                update(strategic_memories)
                .where(strategic_memories.c.id == memory_id)
                .values(category=category.value, updated_at=_utc(now))
            )
            if result.rowcount == 0:
                raise LookupError(f"unknown strategic memory: {memory_id}")
            await self._snapshot(connection, memory_id, now)

    async def count_strategic_by_status(self) -> dict[str, int]:
        return await self._counts(strategic_memories, strategic_memories.c.status)

    async def count_candidates_by_status(self) -> dict[str, int]:
        return await self._counts(memory_candidates, memory_candidates.c.status)

    async def count_strategic_created_since(self, since: datetime) -> int:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(func.count())
                .select_from(strategic_memories)
                .where(strategic_memories.c.created_at >= _utc(since))
            )
            return int(result.scalar_one())

    async def usage_for_memory(self, memory_id: str, *, limit: int = 50) -> list[MemoryUsage]:
        rows = await self._all(
            select(memory_usage)
            .where(memory_usage.c.memory_id == memory_id)
            .order_by(memory_usage.c.created_at.desc(), memory_usage.c.id)
            .limit(limit)
        )
        return [MemoryUsage.model_validate(_aware(row)) for row in rows]

    async def outcomes_for_memory(
        self, memory_id: str, *, limit: int = 50
    ) -> list[MemoryOutcome]:
        rows = await self._all(
            select(memory_outcomes)
            .where(memory_outcomes.c.memory_id == memory_id)
            .order_by(memory_outcomes.c.created_at.desc(), memory_outcomes.c.id)
            .limit(limit)
        )
        return [MemoryOutcome.model_validate(_aware(row)) for row in rows]

    async def list_conflicts(
        self, *, memory_id: str | None = None, limit: int = 100
    ) -> list[MemoryConflict]:
        statement = select(memory_conflicts)
        if memory_id is not None:
            statement = statement.where(
                or_(
                    memory_conflicts.c.memory_a_id == memory_id,
                    memory_conflicts.c.memory_b_id == memory_id,
                )
            )
        rows = await self._all(
            statement.order_by(memory_conflicts.c.created_at.desc(), memory_conflicts.c.id).limit(
                limit
            )
        )
        return [MemoryConflict.model_validate(_aware(row)) for row in rows]

    # --- Point-in-time reads (research replays) --------------------------------------

    async def search_strategic_as_of(
        self,
        *,
        as_of: datetime,
        symbol: str | None = None,
        strategy: str | None = None,
        market_regime: str | None = None,
        limit: int = 20,
    ) -> list[StrategicMemory]:
        """Like ``search_strategic`` but with status, reliability and usage as they were
        at ``as_of``: nothing learned afterwards can leak into a replay."""

        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        moment = _utc(as_of)
        table = strategic_memories
        statement = select(table).where(
            table.c.created_at <= moment,
            table.c.valid_from <= moment,
            or_(table.c.valid_until.is_(None), table.c.valid_until > moment),
        )
        for column, value in (
            (table.c.symbol, symbol),
            (table.c.strategy, strategy),
            (table.c.market_regime, market_regime),
        ):
            if value is not None:
                statement = statement.where(or_(column == value, column.is_(None)))
        current = [
            StrategicMemory.model_validate(_aware(row)) for row in await self._all(statement)
        ]
        if not current:
            return []
        history = await self._all(
            select(strategic_memory_snapshots)
            .where(
                strategic_memory_snapshots.c.memory_id.in_([memory.id for memory in current]),
                strategic_memory_snapshots.c.recorded_at <= moment,
            )
            .order_by(
                strategic_memory_snapshots.c.recorded_at, strategic_memory_snapshots.c.id
            )
        )
        latest: dict[str, dict[str, Any]] = {}
        for row in history:
            latest[str(row["memory_id"])] = _aware(row)  # later rows win
        past = [_as_of_state(memory, latest.get(memory.id)) for memory in current]
        active = [memory for memory in past if memory.status is KnowledgeStatus.ACTIVE]
        active.sort(key=lambda memory: (-memory.reliability, -memory.importance, memory.id))
        return active[:limit]

    async def _snapshot(
        self, connection: AsyncConnection, memory_id: str, recorded_at: datetime
    ) -> None:
        row = (
            (
                await connection.execute(
                    select(strategic_memories).where(strategic_memories.c.id == memory_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return
        await connection.execute(
            insert(strategic_memory_snapshots).values(
                id=str(uuid4()),
                memory_id=memory_id,
                status=row["status"],
                category=row["category"],
                reliability=row["reliability"],
                successful_uses=row["successful_uses"],
                failed_uses=row["failed_uses"],
                validation_count=row["validation_count"],
                last_validated_at=row["last_validated_at"],
                valid_until=row["valid_until"],
                recorded_at=_utc(recorded_at),
            )
        )

    async def _counts(self, table: Any, column: Any) -> dict[str, int]:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(column, func.count()).select_from(table).group_by(column)
            )
            return {str(status): int(count) for status, count in result.all()}

    async def _latest_version(
        self, connection: AsyncConnection, memory_id: str
    ) -> StrategicMemoryVersion | None:
        result = await connection.execute(
            select(strategic_memory_versions)
            .where(strategic_memory_versions.c.strategic_memory_id == memory_id)
            .order_by(strategic_memory_versions.c.version.desc())
            .limit(1)
        )
        row = result.mappings().first()
        return StrategicMemoryVersion.model_validate(_aware(dict(row))) if row else None

    async def _insert(self, table: Any, values: dict[str, Any]) -> None:
        async with self._database.engine.begin() as connection:
            await connection.execute(insert(table).values(**values))

    async def _one(self, statement: Any) -> dict[str, Any] | None:
        async with self._database.engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return dict(row) if row is not None else None

    async def _all(self, statement: Any) -> list[dict[str, Any]]:
        async with self._database.engine.connect() as connection:
            return [dict(row) for row in (await connection.execute(statement)).mappings().all()]


def _components_json(components: dict[str, Decimal] | None) -> dict[str, str] | None:
    return {key: str(value) for key, value in components.items()} if components else None


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _aware(row: dict[str, Any]) -> dict[str, Any]:
    """SQLite returns naive datetimes; values were written as UTC."""

    return {
        key: value.replace(tzinfo=UTC) if isinstance(value, datetime) and value.tzinfo is None
        else value
        for key, value in row.items()
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("memory timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_of_state(memory: StrategicMemory, snapshot: dict[str, Any] | None) -> StrategicMemory:
    """Rebuild a memory's mutable fields at a past moment.

    Knowledge written before snapshots existed has no history: it is shown as it was
    promoted (ACTIVE, reliability = confidence, no recorded uses), never as it is now.
    """

    if snapshot is None:
        return memory.model_copy(
            update={
                "status": KnowledgeStatus.ACTIVE,
                "reliability": memory.confidence,
                "successful_uses": 0,
                "failed_uses": 0,
                "validation_count": 0,
                "last_validated_at": None,
            }
        )
    return memory.model_copy(
        update={
            "status": KnowledgeStatus(str(snapshot["status"])),
            "category": MemoryType(str(snapshot["category"])),
            "reliability": Decimal(str(snapshot["reliability"])),
            "successful_uses": int(snapshot["successful_uses"]),
            "failed_uses": int(snapshot["failed_uses"]),
            "validation_count": int(snapshot["validation_count"]),
            "last_validated_at": snapshot["last_validated_at"],
        }
    )
