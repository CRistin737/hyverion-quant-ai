from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.memory import (
    KnowledgeStatus,
    MemoryType,
    MemoryUsage,
    SqlMemoryRepository,
    StrategicMemory,
)
from trading_bot.memory.gateway import MemoryGateway
from trading_bot.memory.knowledge_lifecycle import (
    KnowledgeLifecycleManager,
    reliability_for,
)
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vault import FileVaultRepository
from trading_bot.memory.vector import NullVectorBackend

NOW = datetime(2026, 9, 23, tzinfo=UTC)
_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _setup(tmp_path, *, now: datetime = NOW, vault: bool = False):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle-memory.db'}")
    await database.initialize()
    _OPEN.append(database)
    repository = SqlMemoryRepository(database)
    audit = AuditRepository(database)
    manager = KnowledgeLifecycleManager(
        repository=repository,
        audit=audit,
        clock=FixedClock(now),
        vault=FileVaultRepository(tmp_path / "knowledge") if vault else None,
    )
    return repository, audit, manager


def _memory(memory_id: str, **updates: object) -> StrategicMemory:
    values: dict[str, object] = {
        "id": memory_id,
        "knowledge_id": f"KNOW-{memory_id}",
        "title": "QQQ trades closed by target reached mostly win",
        "summary": "summary",
        "category": MemoryType.HYPOTHESIS,
        "symbol": "QQQ",
        "confidence": Decimal("0.6"),
        "reliability": Decimal("0.6"),
        "importance": Decimal("0.5"),
        "valid_from": NOW - timedelta(days=10),
        "last_validated_at": NOW - timedelta(days=10),
        "created_at": NOW - timedelta(days=10),
        "updated_at": NOW - timedelta(days=10),
    }
    values.update(updates)
    return StrategicMemory.model_validate(values)


async def _store(repository: SqlMemoryRepository, memory: StrategicMemory) -> None:
    await repository.create_strategic(
        memory, content="body", created_by="curator", change_reason="x"
    )


async def _shown_for_trade(
    repository: SqlMemoryRepository, audit: AuditRepository, memory_id: str, trade_id: str, pnl: str
) -> None:
    await repository.record_usage(
        MemoryUsage(
            id=str(uuid4()),
            memory_id=memory_id,
            decision_id=trade_id,
            retrieval_score=Decimal("0.5"),
            used_in_prompt=True,
            created_at=NOW,
        )
    )
    await audit.append(
        "trade_evaluations",
        {"trade_id": trade_id, "realized_net_pnl": pnl, "evaluated_at": NOW.isoformat()},
        created_at=NOW,
        asset="QQQ",
    )


def test_reliability_blends_prior_with_track_record_and_staleness() -> None:
    fresh = _memory("m", successful_uses=0, failed_uses=10)
    assert reliability_for(fresh, NOW) == Decimal("0.2")  # (0.6*5 + 0) / 15
    winning = _memory("m", successful_uses=10, failed_uses=0)
    assert reliability_for(winning, NOW) == Decimal("0.8667")
    stale = _memory("m", last_validated_at=NOW - timedelta(days=270))
    assert reliability_for(stale, NOW) == Decimal("0.3")  # one half-life past 90 days


async def test_outcomes_are_recorded_once_and_move_counters(tmp_path) -> None:
    repository, audit, manager = await _setup(tmp_path)
    await _store(repository, _memory("m1"))
    await _shown_for_trade(repository, audit, "m1", "op-win", "3")
    await _shown_for_trade(repository, audit, "m1", "op-loss", "-2")

    assert await manager.record_outcomes() == 2
    assert await manager.record_outcomes() == 0
    memory = await repository.get_strategic("m1")
    assert memory is not None
    assert (memory.successful_uses, memory.failed_uses) == (1, 1)


async def test_failing_knowledge_is_demoted_then_retired_never_deleted(tmp_path) -> None:
    repository, _, manager = await _setup(tmp_path, vault=True)
    await _store(repository, _memory("m1", failed_uses=6))
    report = await manager.run()
    assert report.demoted == ["m1"]
    demoted = await repository.get_strategic("m1")
    assert demoted is not None and demoted.status is KnowledgeStatus.NEEDS_REVALIDATION

    await repository.update_strategic_stats(
        "m1",
        reliability=demoted.reliability,
        successful_uses=0,
        failed_uses=15,
        status=KnowledgeStatus.NEEDS_REVALIDATION,
        now=NOW,
    )
    report = await manager.run()
    assert report.retired == ["m1"]
    retired = await repository.get_strategic("m1")
    assert retired is not None
    assert retired.status is KnowledgeStatus.RETIRED
    assert retired.valid_until == NOW
    assert (tmp_path / "knowledge" / "13-Retired-Knowledge" / "KNOW-m1.md").exists()


async def test_revalidation_restores_knowledge_that_works_again(tmp_path) -> None:
    repository, _, manager = await _setup(tmp_path)
    await _store(
        repository,
        _memory("m1", status=KnowledgeStatus.NEEDS_REVALIDATION, successful_uses=9, failed_uses=1),
    )
    report = await manager.run()
    assert report.restored == ["m1"]
    restored = await repository.get_strategic("m1")
    assert restored is not None
    assert restored.status is KnowledgeStatus.ACTIVE
    assert restored.last_validated_at == NOW
    assert restored.validation_count == 1


async def test_stale_knowledge_needs_revalidation(tmp_path) -> None:
    repository, _, manager = await _setup(tmp_path)
    old = NOW - timedelta(days=120)
    await _store(repository, _memory("m1", last_validated_at=old, created_at=old, valid_from=old))
    report = await manager.run()
    assert report.demoted == ["m1"]


async def test_opposing_claims_are_recorded_once_and_weaker_side_revalidated(tmp_path) -> None:
    repository, _, manager = await _setup(tmp_path)
    await _store(repository, _memory("win", reliability=Decimal("0.7"), confidence=Decimal("0.7")))
    await _store(
        repository,
        _memory(
            "lose",
            title="QQQ trades closed by target reached mostly lose",
            category=MemoryType.FAILURE,
            reliability=Decimal("0.4"),
            confidence=Decimal("0.4"),
        ),
    )
    await _store(repository, _memory("eth", symbol="SPY"))

    first = await manager.run()
    second = await manager.run()

    assert first.conflicts == [("win", "lose")] or first.conflicts == [("lose", "win")]
    assert "lose" in first.demoted
    assert second.conflicts == []
    winner = await repository.get_strategic("win")
    assert winner is not None and winner.status is KnowledgeStatus.ACTIVE


async def test_pipeline_usage_is_linked_to_the_operation(tmp_path) -> None:
    repository, _, _ = await _setup(tmp_path)
    await _store(repository, _memory("m1"))
    gateway = MemoryGateway(
        repository=repository,
        retrieval=MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend()),
    )
    await gateway.context(
        agent_id="strategy", query="x", as_of=NOW, symbol="QQQ", decision_id="cycle-1"
    )
    await gateway.link_decision("cycle-1", "op-1")

    assert [usage.memory_id for usage in await repository.usage_for_decision("op-1")] == ["m1"]
    assert await repository.usage_for_decision("cycle-1") == []
