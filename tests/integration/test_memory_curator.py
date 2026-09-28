from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.memory import (
    CandidateStatus,
    KnowledgeStatus,
    MemoryProposal,
    MemoryType,
    SqlMemoryRepository,
)
from trading_bot.memory.curation import (
    AuditEvidenceProvider,
    EvidenceSample,
    MemoryCurator,
    score_evidence,
)
from trading_bot.memory.gateway import MemoryGateway
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


class _Evidence:
    def __init__(self, pnls: Sequence[str | None], *, source: str = "real") -> None:
        self._by_id = {f"op-{index}": pnl for index, pnl in enumerate(pnls)}
        self._source = source

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    async def samples(self, trade_ids: Sequence[str]) -> list[EvidenceSample]:
        samples = []
        for position, trade_id in enumerate(trade_ids):
            pnl = self._by_id.get(trade_id)
            if pnl is None:
                samples.append(EvidenceSample(trade_id=trade_id, valid=False))
                continue
            samples.append(
                EvidenceSample(
                    trade_id=trade_id,
                    valid=True,
                    net_pnl=Decimal(pnl),
                    source=self._source,  # type: ignore[arg-type]
                    observed_at=NOW - timedelta(days=len(trade_ids) - position),
                )
            )
        return samples


async def _setup(tmp_path, evidence, *, vault: FileVaultRepository | None = None):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'curator.db'}")
    await database.initialize()
    _OPEN.append(database)
    repository = SqlMemoryRepository(database)
    audit = AuditRepository(database)
    curator = MemoryCurator(
        repository=repository,
        evidence=evidence,
        audit=audit,
        clock=FixedClock(NOW),
        vault=vault,
    )
    gateway = MemoryGateway(
        repository=repository,
        retrieval=MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend()),
    )
    return repository, audit, curator, gateway


def _proposal(source_ids: tuple[str, ...], **updates: object) -> MemoryProposal:
    values: dict[str, object] = {
        "memory_type": MemoryType.LESSON,
        "title": "Trend entries after pullback work",
        "summary": "Entries after a pullback in uptrends were profitable.",
        "content": "Observed on BTC trend entries.",
        "agent_id": "critic",
        "source_type": "operation",
        "source_ids": source_ids,
        "symbol": "QQQ",
        "market_regime": "TRENDING_UP",
        "created_at": NOW - timedelta(days=1),
    }
    values.update(updates)
    return MemoryProposal.model_validate(values)


def test_confidence_is_deterministic_and_penalizes_contradiction() -> None:
    def samples(pnls: list[str]) -> list[EvidenceSample]:
        return [
            EvidenceSample(
                trade_id=f"t{i}",
                valid=True,
                net_pnl=Decimal(p),
                observed_at=NOW - timedelta(days=len(pnls) - i),
            )
            for i, p in enumerate(pnls)
        ]

    winners = ["2", "3", "2.5", "3", "2"] * 3
    consistent = score_evidence(samples(winners), now=NOW, candidate_regime=None)
    mixed = score_evidence(
        samples(["2", "-3", "2.5", "-3", "2"] * 3), now=NOW, candidate_regime=None
    )
    again = score_evidence(samples(winners), now=NOW, candidate_regime=None)

    assert consistent == again
    assert consistent.oos_confirmed
    assert consistent.components["contradiction"] == Decimal("0")
    assert mixed.components["contradiction"] > Decimal("0.3")
    assert consistent.confidence > mixed.confidence
    assert set(consistent.components) >= {
        "sample_size",
        "statistical_strength",
        "source_quality",
        "recency",
        "oos_confirmation",
        "regime_consistency",
        "data_quality",
        "contradiction",
    }


async def test_one_trade_is_not_knowledge(tmp_path) -> None:
    evidence = _Evidence(["5"])
    _repository, _audit, curator, gateway = await _setup(tmp_path, evidence)
    candidate = await gateway.propose(_proposal(evidence.ids))

    result = await curator.curate(candidate)

    assert result.status is CandidateStatus.PENDING
    assert result.reasons == ("insufficient_evidence",)
    assert result.strategic_memory_id is None


async def test_consistent_evidence_promotes_hypothesis_then_pattern(tmp_path) -> None:
    few = _Evidence(["2", "3", "2.5", "3"])
    _repository, _audit, curator, gateway = await _setup(tmp_path, few)
    hypothesis = await curator.curate(await gateway.propose(_proposal(few.ids)))
    assert hypothesis.promoted_as is MemoryType.HYPOTHESIS

    many = _Evidence(["2", "3", "2.5", "3", "2"] * 3)
    repository2, _, curator2, gateway2 = await _setup(tmp_path / "b", many)
    pattern = await curator2.curate(await gateway2.propose(_proposal(many.ids)))
    assert pattern.promoted_as is MemoryType.PATTERN
    assert pattern.strategic_memory_id is not None
    stored = await repository2.get_strategic(pattern.strategic_memory_id)
    assert stored is not None
    assert stored.status is KnowledgeStatus.ACTIVE
    assert stored.reliability == pattern.report.confidence
    candidate = await repository2.get_candidate(pattern.candidate_id)
    assert candidate is not None
    assert candidate.status is CandidateStatus.PROMOTED
    assert candidate.confidence_components is not None
    assert "oos_confirmation" in candidate.confidence_components


async def test_contradicted_failure_and_bad_data_are_rejected(tmp_path) -> None:
    winners = _Evidence(["2", "3", "2.5", "3", "4"])
    _repository, _audit, curator, gateway = await _setup(tmp_path, winners)
    # A FAILURE claims losses; winning evidence contradicts it.
    failure = await gateway.propose(_proposal(winners.ids, memory_type=MemoryType.FAILURE))
    assert (await curator.curate(failure)).reasons == ("evidence_contradicts_claim",)

    broken = _Evidence(["2", None, None, "3"])
    _repository2, _, curator2, gateway2 = await _setup(tmp_path / "c", broken)
    rejected = await curator2.curate(await gateway2.propose(_proposal(broken.ids)))
    assert rejected.reasons == ("evidence_data_quality_low",)


async def test_duplicates_are_rejected_and_vault_note_is_written(tmp_path) -> None:
    evidence = _Evidence(["2", "3", "2.5", "3"])
    vault = FileVaultRepository(tmp_path / "knowledge")
    _repository, audit, curator, gateway = await _setup(tmp_path, evidence, vault=vault)
    first = await curator.curate(await gateway.propose(_proposal(evidence.ids)))
    second = await curator.curate(
        await gateway.propose(_proposal(evidence.ids, title="Trend entries after PULLBACK work!"))
    )

    assert first.promoted_as is MemoryType.HYPOTHESIS
    assert second.reasons == ("duplicate_of_active_knowledge",)
    notes = list((tmp_path / "knowledge").rglob("*.md"))
    assert len(notes) == 1 and notes[0].parent.name == "07-Patterns"
    events = [row["payload"] for row in await audit.recent("system_events")]
    decisions = [event["decision"] for event in events if event["status"] == "MEMORY_CURATION"]
    assert sorted(decisions) == ["PROMOTED", "REJECTED"]


async def test_curate_pending_and_audit_evidence_provider(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    await database.initialize()
    _OPEN.append(database)
    audit = AuditRepository(database)
    for index, pnl in enumerate(["2", "3", "2.5"]):
        await audit.append(
            "trade_evaluations",
            {
                "trade_id": f"op-{index}",
                "realized_net_pnl": pnl,
                "evaluated_at": (NOW - timedelta(days=3 - index)).isoformat(),
            },
            created_at=NOW,
        )
    await audit.append(
        "trade_evaluations",
        {
            "trade_id": "shadow-1",
            "realized_net_pnl": "0",
            "shadow_comparison": {"shadow_trade_pnl_usd": "4"},
            "evaluated_at": NOW.isoformat(),
        },
        created_at=NOW,
    )
    provider = AuditEvidenceProvider(audit)
    samples = await provider.samples(["op-0", "shadow-1", "missing"])
    assert [sample.valid for sample in samples] == [True, True, False]
    assert samples[1].source == "shadow" and samples[1].net_pnl == Decimal("4")

    repository = SqlMemoryRepository(database)
    gateway = MemoryGateway(
        repository=repository,
        retrieval=MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend()),
    )
    await gateway.propose(_proposal(("op-0", "op-1", "op-2")))
    curator = MemoryCurator(
        repository=repository, evidence=provider, audit=audit, clock=FixedClock(NOW)
    )
    results = await curator.curate_pending()
    assert [result.promoted_as for result in results] == [MemoryType.HYPOTHESIS]
    assert await curator.curate_pending() == []
