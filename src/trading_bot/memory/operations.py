"""Operator surface for memory: health, overview, inspection and human decisions.

Shared by the control API (native dashboard) and the ``memory`` CLI so both show
the same numbers. Reads never write. The only mutations are explicit operator
decisions — confirming or retiring knowledge — and each one needs a reason, is
versioned, audited and exported to the vault. Nothing is ever deleted, and none
of this is read by RiskEngine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import JSON, DateTime, Numeric, func, insert, select, text

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.models import MEMORY_TABLES
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.meta import MetaMemoryAnalyzer
from trading_bot.memory.models import (
    CandidateStatus,
    KnowledgeStatus,
    MemoryType,
    StrategicMemory,
)
from trading_bot.memory.ports import WorkingMemoryBackend
from trading_bot.memory.repository import SqlMemoryRepository
from trading_bot.memory.vault import FileVaultRepository, VaultError
from trading_bot.memory.working import WorkingMemoryUnavailable, build_working_memory
from trading_bot.schemas.common import StrictSchema

HealthStatus = Literal["HEALTHY", "DEGRADED", "FAILED"]
_RANK: dict[HealthStatus, int] = {"HEALTHY": 0, "DEGRADED": 1, "FAILED": 2}
# Only suggestive knowledge can be confirmed; warnings and rules follow other paths.
CONFIRMABLE = frozenset({MemoryType.HYPOTHESIS, MemoryType.PATTERN, MemoryType.LESSON})
MIN_USES_TO_CONFIRM = 5
MIN_RELIABILITY_TO_CONFIRM = Decimal("0.6")
PROBE_KEY = "health:probe"
BACKUP_FORMAT = "hyverion-memory-backup/1"


class MemoryOperatorError(ValueError):
    """An operator request that the evidence or the lifecycle does not allow."""


class MemoryDecisionRequest(StrictSchema):
    """A human decision on one knowledge item, sent from the dashboard."""

    action: Literal["confirm", "retire"]
    reason: str = Field(min_length=3, max_length=200)


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: str
    status: HealthStatus
    detail: str


@dataclass(frozen=True, slots=True)
class MemoryHealthReport:
    overall: HealthStatus
    components: tuple[ComponentHealth, ...]


class MemoryHealthService:
    """HEALTHY / DEGRADED / FAILED for every memory layer, without side effects."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        clock: Clock,
        working: WorkingMemoryBackend | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._clock = clock
        self._working = working

    async def check(self) -> MemoryHealthReport:
        postgres = self._database.url.startswith("postgresql")
        extensions = await self._extensions() if postgres else set()
        components = (
            await self._working_memory(),
            await self._historical(),
            _optional_extension("timescale", "timescaledb", postgres, extensions),
            self._pgvector(postgres, extensions),
            self._embeddings(),
            await asyncio.to_thread(self._directory, "parquet", self._parquet_root()),
            await self._vault(),
        )
        overall = max((item.status for item in components), key=_RANK.__getitem__)
        return MemoryHealthReport(overall=overall, components=components)

    async def _working_memory(self) -> ComponentHealth:
        config = self._settings.public.memory
        production = self._settings.public.app.environment == "production"
        if config.working_backend == "in_process" and self._working is None:
            if production:
                return ComponentHealth("working_memory", "FAILED", "production requires Redis")
            return ComponentHealth(
                "working_memory", "DEGRADED", "in-process: single process, not shared"
            )
        working = self._working
        try:
            if working is None:
                secret = self._settings.secrets.redis_url
                working = build_working_memory(
                    environment=self._settings.public.app.environment,
                    backend=config.working_backend,
                    clock=self._clock,
                    redis_url=secret.get_secret_value() if secret is not None else None,
                    prefix=config.working_key_prefix,
                )
            stamp = self._clock.now().isoformat()
            await working.set(PROBE_KEY, {"at": stamp}, ttl=timedelta(seconds=30))
            echoed = await working.get(PROBE_KEY)
            await working.delete(PROBE_KEY)
        except (WorkingMemoryUnavailable, OSError) as exc:
            return ComponentHealth("working_memory", "FAILED", str(exc)[:160])
        except ValueError as exc:
            # e.g. a malformed REDIS_URL; report the type only so no URL is echoed.
            return ComponentHealth("working_memory", "FAILED", type(exc).__name__)
        finally:
            if self._working is None and working is not None:
                await _close(working)
        if echoed != {"at": stamp}:
            return ComponentHealth("working_memory", "FAILED", "probe did not round-trip")
        return ComponentHealth("working_memory", "HEALTHY", config.working_backend)

    async def _historical(self) -> ComponentHealth:
        healthy, detail = await self._database.healthcheck()
        if not healthy:
            return ComponentHealth("historical_db", "FAILED", detail[:160])
        dialect = "postgresql" if self._database.url.startswith("postgresql") else "sqlite"
        return ComponentHealth("historical_db", "HEALTHY", dialect)

    async def _extensions(self) -> set[str]:
        try:
            async with self._database.engine.connect() as connection:
                result = await connection.execute(text("SELECT extname FROM pg_extension"))
                return {str(row[0]) for row in result.all()}
        except Exception:  # health must report, never raise
            return set()

    @staticmethod
    def _pgvector(postgres: bool, extensions: set[str]) -> ComponentHealth:
        if not postgres:
            return ComponentHealth("pgvector", "DEGRADED", "SQLite: semantic search off")
        if "vector" in extensions:
            return ComponentHealth("pgvector", "HEALTHY", "extension installed")
        return ComponentHealth("pgvector", "FAILED", "extension missing on PostgreSQL")

    def _embeddings(self) -> ComponentHealth:
        model_dir = self._settings.public.memory.embedding_model_dir
        missing = [
            name for name in ("model.onnx", "tokenizer.json") if not (model_dir / name).is_file()
        ]
        if missing:
            return ComponentHealth(
                "embeddings", "DEGRADED", "model not fetched: " + ", ".join(missing)
            )
        return ComponentHealth(
            "embeddings", "HEALTHY", "local model present; hashes checked on load"
        )

    def _parquet_root(self) -> Path:
        return self._settings.public.database.parquet_root

    @staticmethod
    def _directory(component: str, path: Path) -> ComponentHealth:
        if not path.is_dir():
            return ComponentHealth(component, "DEGRADED", f"{path} does not exist yet")
        if not os.access(path, os.W_OK):
            return ComponentHealth(component, "FAILED", f"{path} is not writable")
        return ComponentHealth(component, "HEALTHY", str(path))

    async def _vault(self) -> ComponentHealth:
        root = self._settings.public.memory.vault_path
        base = await asyncio.to_thread(self._directory, "vault", root)
        if base.status != "HEALTHY":
            return base
        vault = FileVaultRepository(root)
        repository = SqlMemoryRepository(self._database)
        drifted = missing = 0
        for memory in await repository.list_strategic(frozenset(KnowledgeStatus), limit=500):
            versions = await repository.versions(memory.id)
            path = vault.note_path(memory)
            if not versions:
                continue
            if not (vault.root / path).is_file():
                missing += 1
            elif await asyncio.to_thread(vault.has_drifted, path, versions[-1]):
                drifted += 1
        if drifted or missing:
            return ComponentHealth(
                "vault", "DEGRADED", f"{missing} notes missing, {drifted} edited outside the app"
            )
        return ComponentHealth("vault", "HEALTHY", str(root))


def _optional_extension(
    component: str, extension: str, postgres: bool, extensions: set[str]
) -> ComponentHealth:
    if not postgres:
        return ComponentHealth(component, "DEGRADED", "SQLite: not applicable")
    if extension in extensions:
        return ComponentHealth(component, "HEALTHY", "extension installed")
    return ComponentHealth(component, "DEGRADED", "optional extension not installed")


async def _close(working: WorkingMemoryBackend) -> None:
    close = getattr(working, "aclose", None)
    if close is not None:
        await close()


@dataclass(slots=True)
class MemoryOverview:
    knowledge: dict[str, int]
    candidates: dict[str, int]
    conflicts: int
    created_last_7d: int
    created_last_30d: int
    retrieval_latency_ms: float
    last_cycle_at: str | None
    recent_lessons: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)


class MemoryOperations:
    def __init__(
        self,
        *,
        database: Database,
        clock: Clock,
        vault: FileVaultRepository | None = None,
    ) -> None:
        self._database = database
        self._repository = SqlMemoryRepository(database)
        self._audit = AuditRepository(database)
        self._clock = clock
        self._vault = vault

    @property
    def repository(self) -> SqlMemoryRepository:
        return self._repository

    async def overview(self) -> MemoryOverview:
        now = self._clock.now()
        started = time.perf_counter()
        await self._repository.search_strategic(as_of=now, limit=10)
        latency = round((time.perf_counter() - started) * 1000, 2)
        lessons = await self._repository.browse_strategic(
            statuses=frozenset({KnowledgeStatus.ACTIVE}), limit=100
        )
        meta = await MetaMemoryAnalyzer(
            repository=self._repository, audit=self._audit, clock=self._clock
        ).evaluate()
        return MemoryOverview(
            knowledge=await self._repository.count_strategic_by_status(),
            candidates=await self._repository.count_candidates_by_status(),
            conflicts=len(await self._repository.list_conflicts(limit=500)),
            created_last_7d=await self._repository.count_strategic_created_since(
                now - timedelta(days=7)
            ),
            created_last_30d=await self._repository.count_strategic_created_since(
                now - timedelta(days=30)
            ),
            retrieval_latency_ms=latency,
            last_cycle_at=await self._last_cycle_at(),
            recent_lessons=[
                memory_summary(memory)
                for memory in lessons
                if memory.category in {MemoryType.LESSON, MemoryType.FAILURE}
            ][:5],
            agents=[_plain(asdict(item)) for item in meta.agents],
        )

    async def knowledge(self, **filters: Any) -> list[dict[str, Any]]:
        memories = await self._repository.browse_strategic(**filters)
        values = await self._values()
        return [
            {**memory_summary(memory), **values.get(memory.id, {})} for memory in memories
        ]

    async def inspect(self, identifier: str) -> dict[str, Any]:
        memory = await self._repository.find_strategic(identifier)
        if memory is None:
            raise LookupError(f"unknown memory: {identifier}")
        versions = await self._repository.versions(memory.id)
        candidate_id = _source_candidate(versions[0].change_reason) if versions else None
        candidate = (
            await self._repository.get_candidate(candidate_id) if candidate_id else None
        )
        evidence = await self._repository.evidence(candidate_id) if candidate_id else []
        outcomes = await self._repository.outcomes_for_memory(memory.id)
        detail: dict[str, Any] = _plain(
            {
                "memory": memory.model_dump(mode="json"),
                "performance": (await self._values()).get(memory.id, {}),
                "versions": [
                    version.model_dump(mode="json", exclude={"content"}) for version in versions
                ],
                "content": versions[-1].content if versions else "",
                "source_candidate": (
                    candidate.model_dump(mode="json") if candidate is not None else None
                ),
                "evidence": [link.model_dump(mode="json") for link in evidence],
                "usage": [
                    usage.model_dump(mode="json")
                    for usage in await self._repository.usage_for_memory(memory.id)
                ],
                "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
                "related_trades": sorted({outcome.trade_id for outcome in outcomes}),
                "conflicts": [
                    conflict.model_dump(mode="json")
                    for conflict in await self._repository.list_conflicts(memory_id=memory.id)
                ],
            }
        )
        return detail

    async def candidates(self, *, include_closed: bool = False) -> list[dict[str, Any]]:
        statuses = (
            frozenset(CandidateStatus)
            if include_closed
            else frozenset({CandidateStatus.PENDING, CandidateStatus.VALIDATING})
        )
        return [
            candidate.model_dump(
                mode="json",
                include={
                    "id",
                    "memory_type",
                    "title",
                    "agent_id",
                    "symbol",
                    "market_regime",
                    "confidence",
                    "status",
                    "created_at",
                },
            )
            for candidate in await self._repository.list_candidates(statuses, limit=200)
        ]

    async def conflicts(self) -> list[dict[str, Any]]:
        return [
            conflict.model_dump(mode="json")
            for conflict in await self._repository.list_conflicts(limit=200)
        ]

    async def confirm(self, identifier: str, *, reason: str, operator: str) -> StrategicMemory:
        """Human confirmation: the only path to CONFIRMED_KNOWLEDGE, and only with evidence."""

        memory = await self._require(identifier, reason)
        uses = memory.successful_uses + memory.failed_uses
        if memory.status is not KnowledgeStatus.ACTIVE:
            raise MemoryOperatorError("only ACTIVE knowledge can be confirmed")
        if memory.category not in CONFIRMABLE:
            raise MemoryOperatorError(f"{memory.category.value} cannot be confirmed")
        if uses < MIN_USES_TO_CONFIRM or memory.reliability < MIN_RELIABILITY_TO_CONFIRM:
            raise MemoryOperatorError(
                f"needs >= {MIN_USES_TO_CONFIRM} evaluated uses and reliability >= "
                f"{MIN_RELIABILITY_TO_CONFIRM} (has {uses} uses, {memory.reliability})"
            )
        if memory.failed_uses >= memory.successful_uses:
            raise MemoryOperatorError("more failures than successes; cannot confirm")
        now = self._clock.now()
        await self._repository.set_strategic_category(
            memory.id, MemoryType.CONFIRMED_KNOWLEDGE, now=now
        )
        await self._decide(memory, "CONFIRMED", reason, operator, now)
        return await self._reload(memory.id)

    async def retire(self, identifier: str, *, reason: str, operator: str) -> StrategicMemory:
        memory = await self._require(identifier, reason)
        if memory.status is KnowledgeStatus.RETIRED:
            raise MemoryOperatorError("knowledge is already retired")
        now = self._clock.now()
        await self._repository.set_strategic_status(memory.id, KnowledgeStatus.RETIRED, now=now)
        await self._decide(memory, "RETIRED", reason, operator, now)
        return await self._reload(memory.id)

    async def backup(self, destination: Path) -> dict[str, Any]:
        """Portable, hash-stamped JSON export of every memory table (SQLite or PostgreSQL)."""

        tables: dict[str, list[dict[str, Any]]] = {}
        async with self._database.engine.connect() as connection:
            for table in MEMORY_TABLES:
                rows = (await connection.execute(select(table))).mappings().all()
                tables[table.name] = [_plain(dict(row)) for row in rows]
        document = {
            "format": BACKUP_FORMAT,
            "created_at": self._clock.now().isoformat(),
            "tables": tables,
        }
        encoded = json.dumps(document, sort_keys=True, ensure_ascii=False, indent=1)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        await asyncio.to_thread(_write_atomic, destination, encoded)
        return {
            "path": str(destination),
            "sha256": digest,
            "rows": {name: len(rows) for name, rows in tables.items()},
        }

    async def restore(
        self, source: Path, *, expected_sha256: str | None = None
    ) -> dict[str, int]:
        """Load a ``backup`` file into an empty memory store, all-or-nothing.

        Fails closed: wrong format, wrong hash, unknown tables or columns, or any
        existing memory row abort the restore before anything is written.
        """

        raw = await asyncio.to_thread(source.read_bytes)
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256.strip().lower():
            raise MemoryOperatorError("backup SHA-256 does not match")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemoryOperatorError("backup is not valid JSON") from exc
        if not isinstance(document, dict) or document.get("format") != BACKUP_FORMAT:
            raise MemoryOperatorError("not a Hyverion memory backup")
        tables = document.get("tables")
        known = {table.name: table for table in MEMORY_TABLES}
        if not isinstance(tables, dict) or not set(tables) <= set(known):
            raise MemoryOperatorError("backup contains unknown tables")
        prepared: list[tuple[Any, list[dict[str, Any]]]] = []
        for table in MEMORY_TABLES:  # foreign-key order
            rows = tables.get(table.name, [])
            if not isinstance(rows, list):
                raise MemoryOperatorError(f"{table.name} is not a list of rows")
            prepared.append((table, [_restore_row(table, row) for row in rows]))
        counts: dict[str, int] = {}
        async with self._database.engine.begin() as connection:
            for table in MEMORY_TABLES:
                existing = await connection.execute(select(func.count()).select_from(table))
                if int(existing.scalar_one()):
                    raise MemoryOperatorError(
                        f"{table.name} is not empty; restore only into an empty memory store"
                    )
            for table, rows in prepared:
                if rows:
                    await connection.execute(insert(table), rows)
                counts[table.name] = len(rows)
        await self._audit.append(
            "system_events",
            {"status": "MEMORY_RESTORED", "sha256": digest, "rows": counts},
            created_at=self._clock.now(),
        )
        return counts

    async def export_vault(self) -> int:
        """Regenerate every note from the database (the vault is derived, never authoritative)."""

        if self._vault is None:
            raise MemoryOperatorError("no vault configured")
        self._vault.ensure_layout()
        memories = await self._repository.list_strategic(frozenset(KnowledgeStatus), limit=500)
        knowledge_ids = {memory.id: memory.knowledge_id for memory in memories}
        written = 0
        for memory in memories:
            versions = await self._repository.versions(memory.id)
            if versions:
                links = await self._conflict_links(memory.id, knowledge_ids)
                await self._vault.write(memory, versions[-1], conflicts=links)
                written += 1
        self._vault.write_indexes(memories)
        return written

    async def _conflict_links(self, memory_id: str, knowledge_ids: dict[str, str]) -> list[str]:
        links: list[str] = []
        for conflict in await self._repository.list_conflicts(memory_id=memory_id):
            other = (
                conflict.memory_b_id
                if conflict.memory_a_id == memory_id
                else conflict.memory_a_id
            )
            if other in knowledge_ids:
                links.append(knowledge_ids[other])
        return links

    async def _require(self, identifier: str, reason: str) -> StrategicMemory:
        if not 3 <= len(reason.strip()) <= 200:
            raise MemoryOperatorError("a reason of 3-200 characters is required")
        memory = await self._repository.find_strategic(identifier)
        if memory is None:
            raise LookupError(f"unknown memory: {identifier}")
        return memory

    async def _decide(
        self, memory: StrategicMemory, decision: str, reason: str, operator: str, now: datetime
    ) -> None:
        versions = await self._repository.versions(memory.id)
        content = versions[-1].content if versions else memory.summary
        note = f"\n\nOperator decision {now.date().isoformat()}: {decision} — {reason.strip()}"
        await self._repository.revise_strategic(
            memory.id,
            content=(content + note)[:20000],
            created_by=operator[:64],
            change_reason=f"operator_{decision.lower()}: {reason.strip()}"[:255],
            now=now,
        )
        await self._audit.append(
            "system_events",
            {
                "status": "MEMORY_OPERATOR_DECISION",
                "memory_id": memory.id,
                "knowledge_id": memory.knowledge_id,
                "decision": decision,
                "reason": reason.strip(),
                "operator": operator[:64],
            },
            created_at=now,
            asset=memory.symbol,
        )
        if self._vault is not None:
            updated = await self._reload(memory.id)
            latest = await self._repository.versions(memory.id)
            try:
                await self._vault.write(updated, latest[-1])
            except (OSError, VaultError):
                pass  # the database stays authoritative; vault drift shows in health

    async def _reload(self, memory_id: str) -> StrategicMemory:
        memory = await self._repository.get_strategic(memory_id)
        if memory is None:  # pragma: no cover - memories are never deleted
            raise LookupError(memory_id)
        return memory

    async def _values(self) -> dict[str, dict[str, Any]]:
        meta = await MetaMemoryAnalyzer(
            repository=self._repository, audit=self._audit, clock=self._clock
        ).evaluate()
        return {
            item.memory_id: {
                "value_score": str(item.value_score),
                "hit_rate": str(item.hit_rate),
                "lift_usd": str(item.lift_usd),
                "evaluated_uses": item.uses,
                "avoided_loss_usd": str(item.avoided_loss_usd),
                "forgone_profit_usd": str(item.forgone_profit_usd),
            }
            for item in meta.memories
        }

    async def _last_cycle_at(self) -> str | None:
        for row in await self._audit.with_status(
            "system_events", ["MEMORY_LIFECYCLE", "MEMORY_META"]
        ):
            payload = row.get("payload")
            if isinstance(payload, dict) and payload.get("status") in {
                "MEMORY_LIFECYCLE",
                "MEMORY_META",
            }:
                created = row.get("created_at")
                if isinstance(created, datetime):
                    aware = created if created.tzinfo else created.replace(tzinfo=UTC)
                    return aware.astimezone(UTC).isoformat()
                return str(created)
        return None


def memory_summary(memory: StrategicMemory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "knowledge_id": memory.knowledge_id,
        "title": memory.title,
        "category": memory.category.value,
        "status": memory.status.value,
        "symbol": memory.symbol,
        "strategy": memory.strategy,
        "market_regime": memory.market_regime,
        "reliability": str(memory.reliability),
        "successful_uses": memory.successful_uses,
        "failed_uses": memory.failed_uses,
        "last_validated_at": (
            memory.last_validated_at.isoformat() if memory.last_validated_at else None
        ),
    }


def _source_candidate(change_reason: str) -> str | None:
    prefix = "promoted_from_candidate:"
    return change_reason[len(prefix) :] if change_reason.startswith(prefix) else None


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write_atomic(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(destination)


def _restore_row(table: Any, row: object) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise MemoryOperatorError(f"{table.name} contains a non-object row")
    unknown = set(row) - set(table.c.keys())
    if unknown:
        raise MemoryOperatorError(f"{table.name} has unknown columns: {sorted(unknown)[:3]}")
    values: dict[str, Any] = {}
    for column in table.c:
        value = row.get(column.name)
        try:
            if value is not None and isinstance(column.type, DateTime):
                parsed = datetime.fromisoformat(str(value))
                value = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            elif value is not None and isinstance(column.type, Numeric):
                value = Decimal(str(value))
            elif value is not None and isinstance(column.type, JSON):
                if not isinstance(value, dict | list):
                    raise ValueError("JSON columns hold objects or lists")
        except (ValueError, InvalidOperation) as exc:
            raise MemoryOperatorError(f"{table.name}.{column.name} is malformed") from exc
        values[column.name] = value
    return values
