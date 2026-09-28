from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_bot.config import load_settings
from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.memory import CandidateStatus, MemoryType, SqlMemoryRepository
from trading_bot.memory.curation import AuditEvidenceProvider, MemoryCurator
from trading_bot.memory.cycle import run_memory_cycle
from trading_bot.memory.distiller import TradeReviewDistiller
from trading_bot.memory.gateway import MemoryGateway
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vector import NullVectorBackend

NOW = datetime(2026, 9, 23, tzinfo=UTC)
_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _setup(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'distill.db'}")
    await database.initialize()
    _OPEN.append(database)
    audit = AuditRepository(database)
    repository = SqlMemoryRepository(database)
    gateway = MemoryGateway(
        repository=repository,
        retrieval=MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend()),
    )
    distiller = TradeReviewDistiller(
        audit=audit, repository=repository, gateway=gateway, clock=FixedClock(NOW)
    )
    return database, audit, repository, distiller


async def _evaluation(
    audit: AuditRepository,
    trade_id: str,
    pnl: str,
    *,
    asset: str = "QQQ",
    exit_reason: str = "protective_stop_reached",
    age_days: float = 1,
    shadow: bool = False,
) -> None:
    payload: dict[str, object] = {
        "trade_id": trade_id,
        "realized_net_pnl": "0" if shadow else pnl,
        "exit_reason": exit_reason,
        "evaluated_at": (NOW - timedelta(days=age_days)).isoformat(),
    }
    if shadow:
        payload["shadow_comparison"] = {"shadow_trade_pnl_usd": pnl}
    await audit.append("trade_evaluations", payload, created_at=NOW, asset=asset)


async def test_losing_stop_exits_become_a_failure_candidate(tmp_path) -> None:
    _, audit, repository, distiller = await _setup(tmp_path)
    for index, pnl in enumerate(["-2", "-3", "-1.5", "1"]):
        await _evaluation(audit, f"op-{index}", pnl, age_days=4 - index)
    await _evaluation(audit, "op-other", "-9", asset="SPY")  # too few for ETH

    report = await distiller.daily_review()

    assert report.trades_considered == 5
    assert len(report.created) == 1
    candidate = await repository.get_candidate(report.created[0])
    assert candidate is not None
    assert candidate.memory_type is MemoryType.FAILURE
    assert candidate.agent_id == "distiller"
    assert candidate.title == "QQQ trades closed by protective stop reached mostly lose"
    assert set(candidate.source_ids) == {"op-0", "op-1", "op-2", "op-3"}


async def test_window_duplicates_and_minimum_evidence(tmp_path) -> None:
    _, audit, _, distiller = await _setup(tmp_path)
    for index in range(3):
        await _evaluation(audit, f"old-{index}", "5", exit_reason="target_reached", age_days=20)

    assert (await distiller.daily_review()).created == ()  # outside the 7-day window
    assert (await distiller.weekly_synthesis()).created == ()  # 3 < weekly minimum of 5
    for index in range(3):
        await _evaluation(audit, f"new-{index}", "5", exit_reason="target_reached")
    first = await distiller.daily_review()
    second = await distiller.daily_review()
    assert len(first.created) == 1
    assert second.created == () and second.skipped_duplicates == 1
    with pytest.raises(ValueError):
        await distiller.distill(window=timedelta(days=1), min_samples=1)


async def test_forgone_shadow_profit_is_informational_only(tmp_path) -> None:
    _, audit, repository, distiller = await _setup(tmp_path)
    for index, pnl in enumerate(["4", "3", "-1"]):
        await _evaluation(audit, f"shadow-{index}", pnl, shadow=True)

    report = await distiller.daily_review()

    candidate = await repository.get_candidate(report.created[0])
    assert candidate is not None
    assert candidate.memory_type is MemoryType.OBSERVATION
    assert "risk limits are unchanged" in candidate.summary


async def test_distilled_candidates_are_curated_from_the_same_evidence(tmp_path) -> None:
    _, audit, repository, distiller = await _setup(tmp_path)
    for index, pnl in enumerate(["2", "3", "2.5", "3"]):
        await _evaluation(
            audit, f"op-{index}", pnl, exit_reason="target_reached", age_days=4 - index
        )
    await distiller.daily_review()

    curator = MemoryCurator(
        repository=repository,
        evidence=AuditEvidenceProvider(audit),
        audit=audit,
        clock=FixedClock(NOW),
    )
    results = await curator.curate_pending()

    assert [result.promoted_as for result in results] == [MemoryType.HYPOTHESIS]
    assert results[0].status is CandidateStatus.PROMOTED


async def test_memory_cycle_runs_end_to_end_and_writes_the_vault(tmp_path) -> None:
    database, audit, _, _ = await _setup(tmp_path)
    for index, pnl in enumerate(["2", "3", "2.5", "3", "2"]):
        await _evaluation(
            audit, f"op-{index}", pnl, exit_reason="target_reached", age_days=5 - index
        )
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

    report = await run_memory_cycle(settings, database, FixedClock(NOW))

    assert len(report.daily.created) == 1
    # The weekly synthesis sees the same open candidate and does not duplicate it.
    assert report.weekly.skipped_duplicates == 1
    assert [result.promoted_as for result in report.curated] == [MemoryType.HYPOTHESIS]
    notes = list((tmp_path / "knowledge").rglob("KNOW-*.md"))
    assert len(notes) == 1
    assert report.meta.outcomes_considered == 5
