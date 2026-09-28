from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trading_bot.db import Database
from trading_bot.memory import (
    CandidateStatus,
    EvidenceLink,
    KnowledgeStatus,
    MemoryCandidate,
    MemoryConflict,
    MemoryOutcome,
    MemoryProposal,
    MemoryType,
    MemoryUsage,
    SqlMemoryRepository,
    StrategicMemory,
)
from trading_bot.memory.ports import MemoryRepository

T0 = datetime(2026, 9, 1, tzinfo=UTC)


_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _repository(tmp_path) -> SqlMemoryRepository:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    await database.initialize()
    _OPEN.append(database)
    return SqlMemoryRepository(database)


def _candidate(**updates: object) -> MemoryCandidate:
    values: dict[str, object] = {
        "id": "cand-1",
        "memory_type": MemoryType.OBSERVATION,
        "title": "Breakout failed in low volume",
        "summary": "Two breakouts on BTC reversed within one hour at low volume.",
        "content": "Evidence: operations op-1 and op-2.",
        "source_type": "trade_evaluation",
        "source_ids": ("op-1", "op-2"),
        "symbol": "QQQ",
        "market_regime": "LOW_VOLATILITY",
        "confidence": Decimal("0.4"),
        "importance": Decimal("0.5"),
        "novelty": Decimal("0.7"),
        "evidence_strength": Decimal("0.3"),
        "created_at": T0,
    }
    values.update(updates)
    return MemoryCandidate.model_validate(values)


def _strategic(memory_id: str = "mem-1", **updates: object) -> StrategicMemory:
    values: dict[str, object] = {
        "id": memory_id,
        "knowledge_id": f"KNOW-{memory_id}",
        "title": "Avoid low-volume breakouts",
        "summary": "Breakouts below median volume underperform.",
        "category": MemoryType.PATTERN,
        "symbol": "QQQ",
        "strategy": "breakout",
        "market_regime": "LOW_VOLATILITY",
        "confidence": Decimal("0.7"),
        "reliability": Decimal("0.6"),
        "importance": Decimal("0.5"),
        "valid_from": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(updates)
    return StrategicMemory.model_validate(values)


async def test_sql_repository_satisfies_the_port(tmp_path) -> None:
    repository: MemoryRepository = await _repository(tmp_path)
    assert repository is not None


async def test_candidate_round_trip_with_evidence_and_status(tmp_path) -> None:
    repository = await _repository(tmp_path)
    link = EvidenceLink(
        id="ev-1",
        memory_candidate_id="cand-1",
        source_type="operation",
        source_id="op-1",
        relationship="supports",
        weight=Decimal("0.8"),
        created_at=T0,
    )
    await repository.add_candidate(_candidate(), [link])
    await repository.set_candidate_status("cand-1", CandidateStatus.VALIDATING)

    stored = await repository.get_candidate("cand-1")
    assert stored is not None
    assert stored.status is CandidateStatus.VALIDATING
    assert stored.source_ids == ("op-1", "op-2")
    assert stored.created_at == T0
    assert stored.confidence == Decimal("0.4")
    assert [item.source_id for item in await repository.evidence("cand-1")] == ["op-1"]


async def test_evidence_must_belong_to_candidate(tmp_path) -> None:
    repository = await _repository(tmp_path)
    foreign = EvidenceLink(
        id="ev-x",
        memory_candidate_id="other",
        source_type="operation",
        source_id="op-1",
        relationship="supports",
        weight=Decimal("1"),
        created_at=T0,
    )
    with pytest.raises(ValueError):
        await repository.add_candidate(_candidate(), [foreign])
    assert await repository.get_candidate("cand-1") is None


async def test_strategic_memory_is_versioned_and_hashed(tmp_path) -> None:
    repository = await _repository(tmp_path)
    first = await repository.create_strategic(
        _strategic(), content="v1 body", created_by="curator", change_reason="promoted"
    )
    later = T0 + timedelta(days=3)
    second = await repository.revise_strategic(
        "mem-1", content="v2 body", created_by="curator", change_reason="revalidated", now=later
    )
    unchanged = await repository.revise_strategic(
        "mem-1", content="v2 body", created_by="curator", change_reason="noop", now=later
    )

    assert first.version == 1
    assert second.version == 2
    assert second.previous_version == 1
    assert second.content_hash != first.content_hash
    assert unchanged.id == second.id
    assert await repository.count_versions("mem-1") == 2
    stored = await repository.get_strategic("mem-1")
    assert stored is not None
    assert stored.updated_at == later


async def test_search_is_time_aware_and_filters_metadata(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.create_strategic(
        _strategic("old"), content="a", created_by="curator", change_reason="x"
    )
    future = T0 + timedelta(days=10)
    await repository.create_strategic(
        _strategic("future", valid_from=future, created_at=future, updated_at=future),
        content="b",
        created_by="curator",
        change_reason="x",
    )
    await repository.create_strategic(
        _strategic("eth", symbol="SPY"), content="c", created_by="curator", change_reason="x"
    )
    await repository.create_strategic(
        _strategic("global", symbol=None, reliability=Decimal("0.9")),
        content="d",
        created_by="curator",
        change_reason="x",
    )

    found = await repository.search_strategic(as_of=T0 + timedelta(days=1), symbol="QQQ")

    # No lookahead: "future" did not exist yet. Global knowledge applies to every symbol.
    assert [memory.id for memory in found] == ["global", "old"]


async def test_retired_knowledge_is_kept_but_not_retrieved(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.create_strategic(
        _strategic(), content="a", created_by="curator", change_reason="x"
    )
    retired_at = T0 + timedelta(days=2)
    await repository.set_strategic_status("mem-1", KnowledgeStatus.RETIRED, now=retired_at)

    assert await repository.search_strategic(as_of=T0 + timedelta(days=5)) == []
    stored = await repository.get_strategic("mem-1")
    assert stored is not None
    assert stored.status is KnowledgeStatus.RETIRED
    assert stored.valid_until == retired_at
    # Before retirement it was valid, so a historical replay still sees it.
    replay = await repository.search_strategic(
        as_of=T0 + timedelta(days=1),
        statuses=frozenset({KnowledgeStatus.ACTIVE, KnowledgeStatus.RETIRED}),
    )
    assert [memory.id for memory in replay] == ["mem-1"]


async def test_usage_outcome_and_conflict_are_recorded(tmp_path) -> None:
    repository = await _repository(tmp_path)
    for memory_id in ("mem-1", "mem-2"):
        await repository.create_strategic(
            _strategic(memory_id), content=memory_id, created_by="curator", change_reason="x"
        )
    await repository.record_usage(
        MemoryUsage(
            id="use-1",
            memory_id="mem-1",
            decision_id="decision-1",
            retrieval_score=Decimal("0.8"),
            used_in_prompt=True,
            created_at=T0,
        )
    )
    await repository.record_outcome(
        MemoryOutcome(
            id="out-1",
            memory_id="mem-1",
            trade_id="op-1",
            prediction_context={"expected": "avoid"},
            actual_outcome="loss_avoided",
            helpful=True,
            estimated_contribution=Decimal("1.25"),
            created_at=T0,
        )
    )
    await repository.record_conflict(
        MemoryConflict(
            id="conf-1", memory_a_id="mem-1", memory_b_id="mem-2", conflict_type="regime",
            created_at=T0,
        )
    )
    with pytest.raises(ValueError):
        await repository.record_conflict(
            MemoryConflict(
                id="conf-2", memory_a_id="mem-1", memory_b_id="mem-1", conflict_type="self",
                created_at=T0,
            )
        )


def test_agents_cannot_propose_strong_knowledge_or_naive_timestamps() -> None:
    base = {
        "title": "t",
        "summary": "s",
        "content": "c",
        "agent_id": "news",
        "source_type": "news_item",
        "source_ids": ["n-1"],
        "created_at": T0,
    }
    assert MemoryProposal.model_validate({**base, "memory_type": "OBSERVATION"})
    for forbidden in ("PATTERN", "RULE", "CONFIRMED_KNOWLEDGE"):
        with pytest.raises(ValidationError):
            MemoryProposal.model_validate({**base, "memory_type": forbidden})
    with pytest.raises(ValidationError):
        MemoryProposal.model_validate(
            {**base, "memory_type": "OBSERVATION", "created_at": datetime(2026, 9, 1)}
        )
    with pytest.raises(ValidationError):
        MemoryProposal.model_validate({**base, "memory_type": "OBSERVATION", "source_ids": []})
