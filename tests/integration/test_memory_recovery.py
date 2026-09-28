"""Phase 12: backup/restore, vault and working-memory recovery, indexing and load."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from trading_bot.config import load_settings
from trading_bot.core.clock import FixedClock
from trading_bot.db import Database
from trading_bot.db.models import MEMORY_TABLES
from trading_bot.main import app as cli_app
from trading_bot.memory import KnowledgeStatus, MemoryType, SqlMemoryRepository, StrategicMemory
from trading_bot.memory.maintenance import MemoryIndexer, embedding_text
from trading_bot.memory.operations import (
    MemoryHealthService,
    MemoryOperations,
    MemoryOperatorError,
)
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vault import FileVaultRepository
from trading_bot.memory.vector import NullVectorBackend

NOW = datetime(2026, 9, 23, tzinfo=UTC)
POSTGRES_URL = os.getenv("HYVERION_TEST_POSTGRES_URL")
MODEL_DIR = Path("data/models/all-MiniLM-L6-v2")
HAS_MODEL = (MODEL_DIR / "model.onnx").is_file() and (MODEL_DIR / "tokenizer.json").is_file()
_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _database(url: str) -> Database:
    database = Database(url)
    await database.initialize()
    _OPEN.append(database)
    return database


def _sqlite(tmp_path: Path, name: str) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / name}"


def _memory(memory_id: str, **updates: object) -> StrategicMemory:
    created = NOW - timedelta(days=3)
    values: dict[str, object] = {
        "id": memory_id,
        "knowledge_id": f"KNOW-{memory_id}",
        "title": f"BTC breakout {memory_id}",
        "summary": "Low-volume breakouts on BTC reverse quickly.",
        "category": MemoryType.HYPOTHESIS,
        "symbol": "QQQ",
        "market_regime": "RANGING",
        "confidence": Decimal("0.6"),
        "reliability": Decimal("0.6"),
        "importance": Decimal("0.5"),
        "valid_from": created,
        "created_at": created,
        "updated_at": created,
    }
    values.update(updates)
    return StrategicMemory.model_validate(values)


async def _seed(repository: SqlMemoryRepository) -> None:
    await repository.create_strategic(
        _memory("m1"), content="Body.", created_by="curator", change_reason="x"
    )
    await repository.update_strategic_stats(
        "m1",
        reliability=Decimal("0.8"),
        successful_uses=4,
        failed_uses=1,
        status=KnowledgeStatus.ACTIVE,
        now=NOW - timedelta(days=1),
    )


async def _row_counts(database: Database) -> dict[str, int]:
    async with database.engine.connect() as connection:
        return {
            table.name: int(
                (await connection.execute(select(func.count()).select_from(table))).scalar_one()
            )
            for table in MEMORY_TABLES
        }


async def test_backup_restores_into_an_empty_store_identically(tmp_path) -> None:
    source = await _database(_sqlite(tmp_path, "source.db"))
    await _seed(SqlMemoryRepository(source))
    backup = tmp_path / "memory.json"
    result = await MemoryOperations(database=source, clock=FixedClock(NOW)).backup(backup)

    target = await _database(_sqlite(tmp_path, "target.db"))
    counts = await MemoryOperations(database=target, clock=FixedClock(NOW)).restore(
        backup, expected_sha256=result["sha256"]
    )

    assert counts == result["rows"]
    assert await _row_counts(target) == await _row_counts(source)
    restored = await SqlMemoryRepository(target).get_strategic("m1")
    assert restored == await SqlMemoryRepository(source).get_strategic("m1")
    # Snapshots survive, so point-in-time research still works after a restore.
    [past] = await SqlMemoryRepository(target).search_strategic_as_of(
        as_of=NOW - timedelta(days=2)
    )
    assert past.reliability == Decimal("0.6")


@pytest.mark.parametrize(
    "corrupt",
    ["sha", "format", "table", "column", "datetime", "json", "not_empty"],
)
async def test_restore_fails_closed_and_writes_nothing(tmp_path, corrupt: str) -> None:
    source = await _database(_sqlite(tmp_path, "source.db"))
    await _seed(SqlMemoryRepository(source))
    backup = tmp_path / "memory.json"
    await MemoryOperations(database=source, clock=FixedClock(NOW)).backup(backup)
    document = json.loads(backup.read_text())
    expected: str | None = None
    target_name = "target.db"
    if corrupt == "sha":
        expected = "0" * 64
    elif corrupt == "format":
        document["format"] = "something-else"
    elif corrupt == "table":
        document["tables"]["positions"] = []
    elif corrupt == "column":
        document["tables"]["strategic_memories"][0]["surprise"] = 1
    elif corrupt == "datetime":
        document["tables"]["strategic_memories"][0]["created_at"] = "yesterday"
    elif corrupt == "json":
        document["tables"]["memory_outcomes"] = [
            {"id": "o", "memory_id": "m1", "trade_id": "t", "prediction_context": "oops",
             "actual_outcome": "win", "created_at": NOW.isoformat()}
        ]
    else:
        target_name = "source.db"  # the source already holds memory
    backup.write_text(json.dumps(document))
    target = await _database(_sqlite(tmp_path, target_name))
    before = await _row_counts(target)

    with pytest.raises(MemoryOperatorError):
        await MemoryOperations(database=target, clock=FixedClock(NOW)).restore(
            backup, expected_sha256=expected
        )
    assert await _row_counts(target) == before


@pytest.mark.skipif(not POSTGRES_URL, reason="HYVERION_TEST_POSTGRES_URL not set")
async def test_sqlite_backup_restores_into_a_fresh_postgres_database(tmp_path) -> None:
    import asyncpg

    assert POSTGRES_URL is not None
    source = await _database(_sqlite(tmp_path, "source.db"))
    await _seed(SqlMemoryRepository(source))
    backup = tmp_path / "memory.json"
    await MemoryOperations(database=source, clock=FixedClock(NOW)).backup(backup)

    admin_dsn = POSTGRES_URL.replace("postgresql+asyncpg://", "postgresql://")
    name = f"hyverion_restore_{uuid4().hex[:8]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        base, _, _ = POSTGRES_URL.rpartition("/")
        target = Database(f"{base}/{name}")
        await target.initialize()
        try:
            await MemoryOperations(database=target, clock=FixedClock(NOW)).restore(backup)
            restored = await SqlMemoryRepository(target).get_strategic("m1")
            assert restored == await SqlMemoryRepository(source).get_strategic("m1")
        finally:
            await target.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_deleted_vault_is_regenerated_from_the_database(tmp_path) -> None:
    database = await _database(_sqlite(tmp_path, "ops.db"))
    repository = SqlMemoryRepository(database)
    await _seed(repository)
    await repository.create_strategic(
        _memory("m2", status=KnowledgeStatus.RETIRED, valid_until=NOW),
        content="Old.",
        created_by="curator",
        change_reason="x",
    )
    vault = FileVaultRepository(tmp_path / "knowledge")
    operations = MemoryOperations(database=database, clock=FixedClock(NOW), vault=vault)

    assert await operations.export_vault() == 2
    assert (tmp_path / "knowledge" / "07-Patterns" / "KNOW-m1.md").exists()
    assert (tmp_path / "knowledge" / "13-Retired-Knowledge" / "KNOW-m2.md").exists()

    settings = load_settings()
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "memory": settings.public.memory.model_copy(
                        update={"vault_path": tmp_path / "knowledge"}
                    )
                }
            )
        }
    )
    report = await MemoryHealthService(
        settings=settings, database=database, clock=FixedClock(NOW)
    ).check()
    assert {c.component: c.status for c in report.components}["vault"] == "HEALTHY"
    with pytest.raises(MemoryOperatorError):
        await MemoryOperations(database=database, clock=FixedClock(NOW)).export_vault()


class _FakeVector:
    available = True

    def __init__(self) -> None:
        self.rows: dict[str, tuple[list[float], datetime, str]] = {}
        self.stamp = NOW

    async def upsert(self, memory_id: str, embedding: Sequence[float], *, model: str) -> None:
        self.rows[memory_id] = (list(embedding), self.stamp, model)

    async def nearest(self, embedding: Sequence[float], *, limit: int) -> list[tuple[str, float]]:
        return []

    async def similarities(
        self, embedding: Sequence[float], memory_ids: Sequence[str]
    ) -> dict[str, float]:
        return {}

    async def indexed(self, memory_ids: Sequence[str], *, model: str) -> dict[str, datetime]:
        return {
            key: self.rows[key][1]
            for key in memory_ids
            if key in self.rows and self.rows[key][2] == model
        }


class _FakeEmbedder:
    model = "fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text))] for text in texts]


async def test_indexer_embeds_new_and_changed_knowledge_only(tmp_path) -> None:
    database = await _database(_sqlite(tmp_path, "index.db"))
    repository = SqlMemoryRepository(database)
    for memory_id in ("a", "b", "c"):
        await repository.create_strategic(
            _memory(memory_id), content="x", created_by="curator", change_reason="x"
        )
    await repository.create_strategic(
        _memory("gone", status=KnowledgeStatus.RETIRED),
        content="x",
        created_by="curator",
        change_reason="x",
    )
    vector, embedder = _FakeVector(), _FakeEmbedder()
    indexer = MemoryIndexer(repository=repository, vector=vector, embedder=embedder, batch_size=2)

    first = await indexer.run()
    assert (first.indexed, first.up_to_date) == (3, 0)
    assert [len(batch) for batch in embedder.calls] == [2, 1]
    assert "gone" not in vector.rows
    assert (await indexer.run()).indexed == 0
    await repository.update_strategic_stats(
        "b",
        reliability=Decimal("0.7"),
        successful_uses=1,
        failed_uses=0,
        status=KnowledgeStatus.ACTIVE,
        now=NOW + timedelta(hours=1),
    )
    vector.stamp = NOW + timedelta(hours=2)  # embeddings written after that change
    assert (await indexer.run()).indexed == 1
    assert (await indexer.run()).indexed == 0
    assert (await indexer.run(force=True)).indexed == 3
    embedder.model = "fake-v2"  # a model upgrade re-embeds everything
    assert (await indexer.run()).indexed == 3
    assert (await indexer.run()).indexed == 0
    unavailable = MemoryIndexer(repository=repository, vector=NullVectorBackend(), embedder=None)
    assert (await unavailable.run()).available is False
    assert "QQQ" in embedding_text(_memory("a"))
    with pytest.raises(ValueError):
        MemoryIndexer(repository=repository, vector=vector, embedder=embedder, batch_size=0)


@pytest.mark.skipif(not POSTGRES_URL or not HAS_MODEL, reason="needs Postgres and the model")
async def test_indexed_knowledge_is_found_semantically_on_postgres() -> None:
    from trading_bot.memory.embeddings import LocalEmbeddingProvider
    from trading_bot.memory.vector import PgVectorBackend

    assert POSTGRES_URL is not None
    database = await _database(POSTGRES_URL)
    vector = PgVectorBackend(database)
    await vector.ensure_schema()
    repository = SqlMemoryRepository(database)
    suffix = uuid4().hex[:8]
    breakout = _memory(
        f"sem-brk-{suffix}",
        title="Low-volume breakouts reverse",
        summary="Breakouts on thin volume fail and reverse within the hour.",
    )
    funding = _memory(
        f"sem-fund-{suffix}",
        title="Funding spikes precede squeezes",
        summary="Extreme perpetual funding rates often end in a short squeeze.",
    )
    for memory in (breakout, funding):
        await repository.create_strategic(
            memory, content="x", created_by="curator", change_reason="x"
        )
    embedder = LocalEmbeddingProvider(MODEL_DIR)

    report = await MemoryIndexer(repository=repository, vector=vector, embedder=embedder).run()
    assert report.indexed >= 2
    indexed = await vector.indexed([breakout.id, funding.id], model=embedder.model)
    assert set(indexed) == {breakout.id, funding.id}
    assert await vector.indexed([breakout.id], model="another-model") == {}

    [query] = await embedder.embed(["thin volume breakout failed"])
    scores = await vector.similarities(query, [breakout.id, funding.id])
    assert scores[breakout.id] > scores[funding.id]


async def test_retrieval_stays_bounded_with_many_memories(tmp_path) -> None:
    database = await _database(_sqlite(tmp_path, "load.db"))
    repository = SqlMemoryRepository(database)
    for index in range(600):
        await repository.create_strategic(
            _memory(
                f"load-{index:04d}",
                reliability=Decimal(index % 100) / Decimal(100),
                symbol="QQQ" if index % 2 else "SPY",
            ),
            content="x",
            created_by="curator",
            change_reason="x",
        )
    live = MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend())
    past = MemoryRetrievalEngine(
        repository=repository, vector=NullVectorBackend(), point_in_time=True
    )

    started = time.perf_counter()
    for _ in range(20):
        result = await live.retrieve("", as_of=NOW, symbol="QQQ", limit=5)
        replay = await past.retrieve("", as_of=NOW, symbol="QQQ", limit=5)
    elapsed = time.perf_counter() - started

    assert len(result.items) == 5 and len(replay.items) == 5
    assert all(item.memory.symbol == "QQQ" for item in result.items)
    assert result.items[0].memory.reliability == Decimal("0.99")
    assert elapsed < 10  # generous CI bound; typically well under a second


def test_recovery_cli_commands(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _sqlite(tmp_path, "cli.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("MEMORY_WORKING_BACKEND", raising=False)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    backup = runner.invoke(cli_app, ["memory", "backup", "--destination", "b.json"])
    assert backup.exit_code == 0, backup.stdout
    for command in (["export-vault"], ["reindex"], ["rebuild-working"]):
        result = runner.invoke(cli_app, ["memory", *command])
        assert result.exit_code == 0, (command, result.stdout)
    # Rebuilding working memory writes no memory rows, so the store is still empty.
    restored = runner.invoke(cli_app, ["memory", "restore", "b.json"])
    assert restored.exit_code == 0, restored.stdout
    refused = runner.invoke(cli_app, ["memory", "restore", "b.json", "--sha256", "0" * 64])
    assert refused.exit_code == 1
    missing = runner.invoke(cli_app, ["memory", "restore", "nope.json"])
    assert missing.exit_code == 1


async def test_export_links_conflicts_and_writes_indexes(tmp_path) -> None:
    from trading_bot.memory import MemoryConflict

    database = await _database(_sqlite(tmp_path, "links.db"))
    repository = SqlMemoryRepository(database)
    for memory_id in ("a", "b"):
        await repository.create_strategic(
            _memory(memory_id), content="x", created_by="curator", change_reason="x"
        )
    await repository.record_conflict(
        MemoryConflict(
            id="c-1", memory_a_id="a", memory_b_id="b", conflict_type="opposing", created_at=NOW
        )
    )
    vault = FileVaultRepository(tmp_path / "knowledge")

    await MemoryOperations(database=database, clock=FixedClock(NOW), vault=vault).export_vault()

    root = tmp_path / "knowledge"
    assert "[[KNOW-b]]" in (root / "07-Patterns" / "KNOW-a.md").read_text()
    assert "[[KNOW-a]]" in (root / "07-Patterns" / "KNOW-b.md").read_text()
    assert (root / "03-Markets" / "QQQ.md").exists()
    assert (root / "04-Regimes" / "ranging.md").exists()
    assert (root / "00-System" / "Knowledge Map.md").exists()
