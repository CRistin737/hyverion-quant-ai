from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete

from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.db.models import strategic_memory_snapshots
from trading_bot.memory import KnowledgeStatus, MemoryType, SqlMemoryRepository, StrategicMemory
from trading_bot.memory.research import MemoryReplayEvaluator
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vector import NullVectorBackend

NOW = datetime(2026, 9, 23, tzinfo=UTC)
DAY = timedelta(days=1)
_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _setup(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'research.db'}")
    await database.initialize()
    _OPEN.append(database)
    return database, SqlMemoryRepository(database), AuditRepository(database)


def _memory(memory_id: str, created: datetime, **updates: object) -> StrategicMemory:
    values: dict[str, object] = {
        "id": memory_id,
        "knowledge_id": f"KNOW-{memory_id}",
        "title": f"memory {memory_id}",
        "summary": "summary",
        "category": MemoryType.HYPOTHESIS,
        "symbol": "QQQ",
        "confidence": Decimal("0.6"),
        "reliability": Decimal("0.6"),
        "importance": Decimal("0.5"),
        "valid_from": created,
        "created_at": created,
        "updated_at": created,
    }
    values.update(updates)
    return StrategicMemory.model_validate(values)


async def _store(repository: SqlMemoryRepository, memory: StrategicMemory) -> None:
    await repository.create_strategic(
        memory, content="body", created_by="curator", change_reason="x"
    )


async def test_point_in_time_search_rebuilds_past_status_and_reliability(tmp_path) -> None:
    _, repository, _ = await _setup(tmp_path)
    t0 = NOW - 30 * DAY
    await _store(repository, _memory("m1", t0))
    await repository.update_strategic_stats(
        "m1",
        reliability=Decimal("0.9"),
        successful_uses=8,
        failed_uses=1,
        status=KnowledgeStatus.ACTIVE,
        now=t0 + 10 * DAY,
    )
    await repository.set_strategic_status("m1", KnowledgeStatus.RETIRED, now=t0 + 20 * DAY)

    async def at(moment: datetime) -> list[StrategicMemory]:
        return await repository.search_strategic_as_of(as_of=moment, symbol="QQQ")

    assert await at(t0 - DAY) == []  # did not exist yet
    early = await at(t0 + 5 * DAY)
    assert [(m.reliability, m.successful_uses) for m in early] == [(Decimal("0.6"), 0)]
    later = await at(t0 + 15 * DAY)
    assert [(m.reliability, m.successful_uses) for m in later] == [(Decimal("0.9"), 8)]
    assert await at(t0 + 25 * DAY) == []  # retired by then

    # The live search leaks the future: it hides knowledge that was active back then.
    live = await repository.search_strategic(as_of=t0 + 5 * DAY, symbol="QQQ")
    assert live == []


async def test_demotion_is_respected_only_while_it_lasted(tmp_path) -> None:
    _, repository, _ = await _setup(tmp_path)
    t0 = NOW - 30 * DAY
    await _store(repository, _memory("m1", t0))
    for offset, status in ((5, KnowledgeStatus.NEEDS_REVALIDATION), (10, KnowledgeStatus.ACTIVE)):
        await repository.update_strategic_stats(
            "m1",
            reliability=Decimal("0.5"),
            successful_uses=1,
            failed_uses=1,
            status=status,
            now=t0 + offset * DAY,
        )

    during = await repository.search_strategic_as_of(as_of=t0 + 7 * DAY)
    after = await repository.search_strategic_as_of(as_of=t0 + 12 * DAY)
    assert during == []
    assert [memory.id for memory in after] == ["m1"]


async def test_knowledge_without_history_is_shown_as_promoted(tmp_path) -> None:
    database, repository, _ = await _setup(tmp_path)
    t0 = NOW - 10 * DAY
    await _store(repository, _memory("m1", t0))
    await repository.update_strategic_stats(
        "m1",
        reliability=Decimal("0.95"),
        successful_uses=20,
        failed_uses=0,
        status=KnowledgeStatus.ACTIVE,
        now=t0 + DAY,
    )
    async with database.engine.begin() as connection:  # simulate pre-0008 knowledge
        await connection.execute(delete(strategic_memory_snapshots))

    [memory] = await repository.search_strategic_as_of(as_of=NOW)
    assert (memory.reliability, memory.successful_uses) == (Decimal("0.6"), 0)


async def test_point_in_time_retrieval_engine(tmp_path) -> None:
    _, repository, _ = await _setup(tmp_path)
    await _store(repository, _memory("m1", NOW - 5 * DAY))
    await repository.set_strategic_status("m1", KnowledgeStatus.RETIRED, now=NOW - DAY)
    engine = MemoryRetrievalEngine(
        repository=repository, vector=NullVectorBackend(), point_in_time=True
    )
    live = MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend())

    past = await engine.retrieve("", as_of=NOW - 3 * DAY, symbol="QQQ")
    assert [item.memory.id for item in past.items] == ["m1"]
    assert (await live.retrieve("", as_of=NOW - 3 * DAY, symbol="QQQ")).items == ()


async def _decision(
    audit: AuditRepository, trade_id: str, decided: datetime, pnl: str, *, shadow: bool = False
) -> None:
    await audit.append(
        "trade_proposals", {"proposal_id": trade_id}, created_at=decided, asset="QQQ"
    )
    payload: dict[str, object] = {
        "trade_id": trade_id,
        "realized_net_pnl": "0" if shadow else pnl,
        "evaluated_at": (decided + DAY).isoformat(),
    }
    if shadow:
        payload["shadow_comparison"] = {"shadow_trade_pnl_usd": pnl}
    await audit.append("trade_evaluations", payload, created_at=decided + DAY, asset="QQQ")


async def test_replay_uses_only_knowledge_that_existed_at_decision_time(tmp_path) -> None:
    _, repository, audit = await _setup(tmp_path)
    await _store(repository, _memory("warn", NOW - 10 * DAY, category=MemoryType.FAILURE))
    await _store(repository, _memory("late", NOW - 2 * DAY))  # created after most decisions
    await _decision(audit, "before-warning", NOW - 20 * DAY, "5")
    await _decision(audit, "warned-loss", NOW - 5 * DAY, "-3")
    await _decision(audit, "warned-shadow", NOW - 4 * DAY, "-2", shadow=True)
    await _decision(audit, "latest", NOW - DAY, "4")
    await audit.append(
        "trade_evaluations",
        {"trade_id": "orphan", "realized_net_pnl": "1", "evaluated_at": NOW.isoformat()},
        created_at=NOW,
        asset="QQQ",
    )

    report = await MemoryReplayEvaluator(
        repository=repository, audit=audit, clock=FixedClock(NOW)
    ).replay(window=timedelta(days=30))

    by_id = {item.trade_id: item for item in report.decisions}
    assert report.leaks == 0
    assert report.skipped_without_decision_time == 1
    assert by_id["before-warning"].warnings == () and by_id["before-warning"].support == ()
    assert by_id["warned-loss"].warnings == ("KNOW-warn",)
    assert by_id["warned-loss"].support == ()  # "late" did not exist yet
    assert by_id["warned-shadow"].shadow is True
    assert set(by_id["latest"].support) == {"KNOW-late"}
    summary = report.summary()
    assert summary["warned"] == 3 and summary["warned_losing"] == 2
    assert summary["total_pnl_usd"] == "4.00"
    assert summary["pnl_if_warned_skipped_usd"] == "5.00"
    events = await audit.recent("system_events", limit=1)
    assert events[0]["payload"]["status"] == "MEMORY_REPLAY"
    with pytest.raises(ValueError):
        await MemoryReplayEvaluator(
            repository=repository, audit=audit, clock=FixedClock(NOW)
        ).replay(window=timedelta(0))
