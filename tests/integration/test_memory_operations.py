from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import control_client
from typer.testing import CliRunner

from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.main import app as cli_app
from trading_bot.memory import (
    EvidenceLink,
    KnowledgeStatus,
    MemoryCandidate,
    MemoryConflict,
    MemoryOutcome,
    MemoryType,
    MemoryUsage,
    SqlMemoryRepository,
    StrategicMemory,
)
from trading_bot.memory.operations import (
    MemoryHealthService,
    MemoryOperations,
    MemoryOperatorError,
)
from trading_bot.memory.vault import FileVaultRepository
from trading_bot.memory.working import InMemoryWorkingMemoryAdapter

NOW = datetime(2026, 9, 23, tzinfo=UTC)
_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


def _settings(tmp_path, *, environment: str = "development"):
    settings = load_settings()
    public = settings.public
    return settings.model_copy(
        update={
            "public": public.model_copy(
                update={
                    "app": public.app.model_copy(update={"environment": environment}),
                    "database": public.database.model_copy(
                        update={
                            "url": f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}",
                            "parquet_root": tmp_path / "parquet",
                        }
                    ),
                    "memory": public.memory.model_copy(
                        update={
                            "vault_path": tmp_path / "knowledge",
                            "embedding_model_dir": tmp_path / "model",
                        }
                    ),
                }
            )
        }
    )


async def _database(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}")
    await database.initialize()
    _OPEN.append(database)
    return database


def _memory(memory_id: str, **updates: object) -> StrategicMemory:
    values: dict[str, object] = {
        "id": memory_id,
        "knowledge_id": f"KNOW-{memory_id}",
        "title": f"BTC breakout {memory_id}",
        "summary": "summary",
        "category": MemoryType.HYPOTHESIS,
        "symbol": "QQQ",
        "strategy": "breakout",
        "market_regime": "TRENDING_UP",
        "confidence": Decimal("0.6"),
        "reliability": Decimal("0.7"),
        "importance": Decimal("0.5"),
        "valid_from": NOW - timedelta(days=2),
        "created_at": NOW - timedelta(days=2),
        "updated_at": NOW - timedelta(days=2),
        "successful_uses": 6,
        "failed_uses": 1,
    }
    values.update(updates)
    return StrategicMemory.model_validate(values)


async def _store(repository: SqlMemoryRepository, memory: StrategicMemory, reason="x") -> None:
    await repository.create_strategic(
        memory, content="Body of the note.", created_by="curator", change_reason=reason
    )


async def _operations(tmp_path, *, vault: bool = True):
    database = await _database(tmp_path)
    store = FileVaultRepository(tmp_path / "knowledge") if vault else None
    if store is not None:
        store.ensure_layout()
    operations = MemoryOperations(database=database, clock=FixedClock(NOW), vault=store)
    return database, operations, operations.repository


async def test_health_reports_every_layer_without_side_effects(tmp_path) -> None:
    database = await _database(tmp_path)
    settings = _settings(tmp_path)

    report = await MemoryHealthService(
        settings=settings, database=database, clock=FixedClock(NOW)
    ).check()

    statuses = {item.component: item.status for item in report.components}
    assert statuses == {
        "working_memory": "DEGRADED",
        "historical_db": "HEALTHY",
        "timescale": "DEGRADED",
        "pgvector": "DEGRADED",
        "embeddings": "DEGRADED",
        "parquet": "DEGRADED",
        "vault": "DEGRADED",
    }
    assert report.overall == "DEGRADED"
    assert not (tmp_path / "knowledge").exists()  # a read never creates anything

    shared = InMemoryWorkingMemoryAdapter(FixedClock(NOW))
    healthy = await MemoryHealthService(
        settings=settings, database=database, clock=FixedClock(NOW), working=shared
    ).check()
    assert healthy.components[0].status == "HEALTHY"
    assert await shared.get("health:probe") is None  # the probe cleans up


async def test_production_without_redis_fails_and_vault_drift_degrades(tmp_path) -> None:
    database = await _database(tmp_path)
    repository = SqlMemoryRepository(database)
    await _store(repository, _memory("m1"))
    vault = FileVaultRepository(tmp_path / "knowledge")
    vault.ensure_layout()
    memory = await repository.get_strategic("m1")
    assert memory is not None
    path = await vault.write(memory, (await repository.versions("m1"))[-1])
    note = tmp_path / "knowledge" / path
    # Notes outside the managed block are the owner's; only the block is authoritative.
    note.write_text(note.read_text().replace("Body of the note.", "Tampered by hand."))

    report = await MemoryHealthService(
        settings=_settings(tmp_path, environment="production"),
        database=database,
        clock=FixedClock(NOW),
    ).check()

    components = {item.component: item for item in report.components}
    assert report.overall == "FAILED"
    assert components["working_memory"].status == "FAILED"
    assert components["vault"].status == "DEGRADED"
    assert "1 edited outside the app" in components["vault"].detail


async def test_browse_filters_and_escapes_like_wildcards(tmp_path) -> None:
    _, operations, repository = await _operations(tmp_path)
    await _store(repository, _memory("a", title="BTC 100% breakout"))
    await _store(repository, _memory("b", symbol="SPY", reliability=Decimal("0.3")))
    await _store(repository, _memory("c", status=KnowledgeStatus.RETIRED))

    everything = await operations.knowledge()
    assert {row["id"] for row in everything} == {"a", "b", "c"}
    literal = await operations.knowledge(text="100%")
    assert [row["id"] for row in literal] == ["a"]
    assert await operations.knowledge(text="100_") == []
    eth = await operations.knowledge(symbol="SPY")
    assert [row["id"] for row in eth] == ["b"]
    reliable = await operations.knowledge(min_reliability=Decimal("0.5"))
    assert {row["id"] for row in reliable} == {"a", "c"}
    active = await operations.knowledge(statuses=frozenset({KnowledgeStatus.ACTIVE}))
    assert {row["id"] for row in active} == {"a", "b"}
    assert (await operations.repository.find_strategic("KNOW-a")) is not None


async def test_inspect_joins_evidence_usage_outcomes_and_conflicts(tmp_path) -> None:
    _, operations, repository = await _operations(tmp_path)
    await repository.add_candidate(
        MemoryCandidate(
            id="cand-1",
            memory_type=MemoryType.HYPOTHESIS,
            title="BTC breakout",
            summary="summary",
            content="content",
            source_type="trade_evaluation",
            source_ids=("op-1",),
            confidence=Decimal("0.6"),
            importance=Decimal("0.5"),
            novelty=Decimal("0.5"),
            evidence_strength=Decimal("0.5"),
            created_at=NOW,
        ),
        [
            EvidenceLink(
                id="ev-1",
                memory_candidate_id="cand-1",
                source_type="operation",
                source_id="op-1",
                relationship="supports",
                weight=Decimal("0.8"),
                created_at=NOW,
            )
        ],
    )
    await _store(repository, _memory("m1"), reason="promoted_from_candidate:cand-1")
    await _store(repository, _memory("m2"))
    await repository.record_usage(
        MemoryUsage(
            id=str(uuid4()),
            memory_id="m1",
            decision_id="op-1",
            retrieval_score=Decimal("0.5"),
            used_in_prompt=True,
            created_at=NOW,
        )
    )
    await repository.record_outcome(
        MemoryOutcome(
            id=str(uuid4()),
            memory_id="m1",
            trade_id="op-1",
            prediction_context={"category": "HYPOTHESIS"},
            actual_outcome="win",
            helpful=True,
            estimated_contribution=Decimal("3"),
            created_at=NOW,
        )
    )
    await repository.record_conflict(
        MemoryConflict(
            id="conf-1",
            memory_a_id="m1",
            memory_b_id="m2",
            conflict_type="opposing_claims",
            created_at=NOW,
        )
    )

    detail = await operations.inspect("KNOW-m1")

    assert detail["memory"]["id"] == "m1"
    assert detail["source_candidate"]["id"] == "cand-1"
    assert [link["source_id"] for link in detail["evidence"]] == ["op-1"]
    assert [usage["decision_id"] for usage in detail["usage"]] == ["op-1"]
    assert detail["related_trades"] == ["op-1"]
    assert [conflict["id"] for conflict in detail["conflicts"]] == ["conf-1"]
    assert detail["content"] == "Body of the note."
    assert "content" not in detail["versions"][0]
    json.dumps(detail)  # fully serialisable for the API and CLI
    with pytest.raises(LookupError):
        await operations.inspect("missing")


async def test_confirmation_requires_evidence_and_is_versioned(tmp_path) -> None:
    database, operations, repository = await _operations(tmp_path)
    await _store(repository, _memory("thin", successful_uses=2, failed_uses=0))
    await _store(repository, _memory("warn", category=MemoryType.FAILURE))
    await _store(repository, _memory("good"))

    with pytest.raises(MemoryOperatorError, match="evaluated uses"):
        await operations.confirm("thin", reason="looks right", operator="me")
    with pytest.raises(MemoryOperatorError, match="cannot be confirmed"):
        await operations.confirm("warn", reason="looks right", operator="me")
    with pytest.raises(MemoryOperatorError, match="reason"):
        await operations.confirm("good", reason="ok", operator="me")

    confirmed = await operations.confirm("KNOW-good", reason="held across 7 trades", operator="me")

    assert confirmed.category is MemoryType.CONFIRMED_KNOWLEDGE
    versions = await repository.versions("good")
    assert [version.version for version in versions] == [1, 2]
    assert versions[-1].change_reason == "operator_confirmed: held across 7 trades"
    assert versions[-1].created_by == "me"
    events = await AuditRepository(database).recent("system_events", limit=5)
    assert events[0]["payload"]["decision"] == "CONFIRMED"
    assert (tmp_path / "knowledge" / "07-Patterns" / "KNOW-good.md").exists()
    with pytest.raises(MemoryOperatorError, match="cannot be confirmed"):
        await operations.confirm("good", reason="again please", operator="me")


async def test_retirement_is_final_and_non_destructive(tmp_path) -> None:
    _, operations, repository = await _operations(tmp_path)
    await _store(repository, _memory("m1"))

    retired = await operations.retire("m1", reason="regime changed", operator="me")

    assert retired.status is KnowledgeStatus.RETIRED
    assert retired.valid_until == NOW
    assert await repository.get_strategic("m1") is not None
    assert (tmp_path / "knowledge" / "13-Retired-Knowledge" / "KNOW-m1.md").exists()
    with pytest.raises(MemoryOperatorError, match="already retired"):
        await operations.retire("m1", reason="regime changed", operator="me")


async def test_backup_is_portable_and_hash_stamped(tmp_path) -> None:
    _, operations, repository = await _operations(tmp_path, vault=False)
    await _store(repository, _memory("m1"))
    target = tmp_path / "backups" / "memory.json"

    result = await operations.backup(target)

    content = target.read_text(encoding="utf-8")
    assert result["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    document = json.loads(content)
    assert document["format"] == "hyverion-memory-backup/1"
    assert result["rows"]["strategic_memories"] == 1
    assert result["rows"]["strategic_memory_versions"] == 1
    assert Decimal(document["tables"]["strategic_memories"][0]["reliability"]) == Decimal("0.7")


async def test_overview_counts_knowledge(tmp_path) -> None:
    _, operations, repository = await _operations(tmp_path)
    await _store(repository, _memory("m1", category=MemoryType.LESSON))
    await _store(repository, _memory("m2", status=KnowledgeStatus.NEEDS_REVALIDATION))

    overview = await operations.overview()

    assert overview.knowledge == {"ACTIVE": 1, "NEEDS_REVALIDATION": 1}
    assert overview.created_last_7d == 2
    assert [lesson["id"] for lesson in overview.recent_lessons] == ["m1"]
    assert overview.retrieval_latency_ms >= 0


def test_control_api_memory_endpoints(tmp_path) -> None:
    settings = _settings(tmp_path)
    with control_client(create_control_api(settings)) as client:
        status = client.get("/api/v1/memory")
        assert status.status_code == 200
        assert status.json()["health"]["overall"] in {"HEALTHY", "DEGRADED", "FAILED"}
        assert client.get("/api/v1/memory/knowledge").json() == []
        assert client.get("/api/v1/memory/knowledge", params={"status": "BAD"}).status_code == 422
        assert (
            client.get("/api/v1/memory/knowledge", params={"min_reliability": "2"}).status_code
            == 422
        )
        assert client.get("/api/v1/memory/knowledge/nope").status_code == 404
        decision = client.post(
            "/api/v1/memory/knowledge/nope/decision",
            json={"action": "retire", "reason": "not useful"},
        )
        assert decision.status_code == 404
        invalid = client.post(
            "/api/v1/memory/knowledge/nope/decision", json={"action": "delete", "reason": "x"}
        )
        assert invalid.status_code == 422
        assert client.get("/api/v1/memory/candidates").json() == []
        assert client.get("/api/v1/memory/conflicts").json() == []
        cycle = client.post("/api/v1/memory/cycle")
        assert cycle.status_code == 200
        assert cycle.json()["outcomes_considered"] == 0


def test_cli_memory_commands(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    search = runner.invoke(cli_app, ["memory", "search", "breakout"])
    assert search.exit_code == 0, search.stdout
    assert json.loads(re.sub(r"\x1b\[[0-9;]*m", "", search.stdout)) == []
    missing = runner.invoke(cli_app, ["memory", "inspect", "KNOW-x"])
    assert missing.exit_code == 1
    refused = runner.invoke(cli_app, ["memory", "retire", "KNOW-x", "--reason", "gone"])
    assert refused.exit_code == 1
    backup = runner.invoke(
        cli_app, ["memory", "backup", "--destination", str(tmp_path / "b.json")]
    )
    assert backup.exit_code == 0
    assert (tmp_path / "b.json").exists()
    for command in ("candidates", "conflicts", "distill", "synthesize", "validate", "replay"):
        result = runner.invoke(cli_app, ["memory", command])
        assert result.exit_code == 0, (command, result.stdout)


@pytest.mark.skipif(
    not (os.getenv("HYVERION_TEST_POSTGRES_URL") and os.getenv("REDIS_URL")),
    reason="docker memory profile not configured",
)
async def test_health_against_postgres_and_redis(tmp_path) -> None:
    from pydantic import SecretStr

    database = Database(os.environ["HYVERION_TEST_POSTGRES_URL"])
    await database.initialize()
    _OPEN.append(database)
    settings = _settings(tmp_path)
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "memory": settings.public.memory.model_copy(
                        update={"working_backend": "redis"}
                    )
                }
            ),
            "secrets": settings.secrets.model_copy(
                update={"redis_url": SecretStr(os.environ["REDIS_URL"])}
            ),
        }
    )

    report = await MemoryHealthService(
        settings=settings, database=database, clock=FixedClock(NOW)
    ).check()

    statuses = {item.component: item.status for item in report.components}
    assert statuses["working_memory"] == "HEALTHY"
    assert statuses["historical_db"] == "HEALTHY"
    assert statuses["pgvector"] == "HEALTHY"
    assert statuses["timescale"] == "DEGRADED"  # optional, not in the pgvector image


async def test_malformed_redis_url_reports_failed_without_echoing_it(tmp_path) -> None:
    from pydantic import SecretStr

    database = await _database(tmp_path)
    settings = _settings(tmp_path)
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "memory": settings.public.memory.model_copy(
                        update={"working_backend": "redis"}
                    )
                }
            ),
            "secrets": settings.secrets.model_copy(
                update={"redis_url": SecretStr("localhost:6379-secret-token")}
            ),
        }
    )

    report = await MemoryHealthService(
        settings=settings, database=database, clock=FixedClock(NOW)
    ).check()

    working = report.components[0]
    assert (working.component, working.status) == ("working_memory", "FAILED")
    assert "secret" not in working.detail
    assert report.overall == "FAILED"
