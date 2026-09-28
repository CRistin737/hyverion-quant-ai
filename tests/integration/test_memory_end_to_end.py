"""The master prompt's success criteria, walked end to end on one SQLite store.

Evidence → candidate → curated knowledge → vault → retrieved in a later decision
→ usefulness measured → reliability drops when it stops working → revalidated
and retired → full audit trail. RiskEngine never sees memory.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import make_context, make_critic, make_proposal

from trading_bot.config import load_settings
from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.memory import KnowledgeStatus, MemoryType, SqlMemoryRepository
from trading_bot.memory.cycle import run_memory_cycle
from trading_bot.memory.gateway import MemoryGateway
from trading_bot.memory.knowledge_lifecycle import KnowledgeLifecycleManager
from trading_bot.memory.maintenance import WorkingMemoryRebuilder
from trading_bot.memory.meta import MetaMemoryAnalyzer
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vault import FileVaultRepository
from trading_bot.memory.vector import NullVectorBackend
from trading_bot.memory.working import InMemoryWorkingMemoryAdapter
from trading_bot.risk.engine import RiskEngine

START = datetime(2026, 9, 1, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[2] / "src" / "trading_bot"


@pytest.fixture
async def database(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    await database.initialize()
    yield database
    await database.close()


async def _evaluated(
    audit: AuditRepository, trade_id: str, pnl: str, at: datetime, reason: str
) -> None:
    await audit.append(
        "trade_evaluations",
        {
            "trade_id": trade_id,
            "realized_net_pnl": pnl,
            "exit_reason": reason,
            "evaluated_at": at.isoformat(),
        },
        created_at=at,
        asset="QQQ",
    )


async def test_memory_learns_is_used_measured_and_retired(database, tmp_path) -> None:
    audit = AuditRepository(database)
    repository = SqlMemoryRepository(database)
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

    # 2. Working memory is disposable and rebuilt from the authoritative database.
    from trading_bot.db import OperationLifecycleRepository, TradingStateRepository

    working = InMemoryWorkingMemoryAdapter(FixedClock(START))
    summary = await WorkingMemoryRebuilder(
        backend=working,
        state=TradingStateRepository(database),
        lifecycle=OperationLifecycleRepository(database),
        clock=FixedClock(START),
    ).rebuild()
    assert await working.get("session:current") == summary

    # 3, 9. Closed trades are evaluated and become queryable history.
    for index in range(5):
        at = START + timedelta(days=index)
        await _evaluated(audit, f"win-{index}", "3", at, "target_reached")
    assert len(await audit.recent("trade_evaluations", limit=50)) == 5

    # 10-13. Enough evidence → candidate → curated knowledge → vault note.
    cycle_at = START + timedelta(days=5)
    report = await run_memory_cycle(settings, database, FixedClock(cycle_at))
    [promoted] = report.curated
    assert promoted.promoted_as is not None
    assert promoted.promoted_as not in {MemoryType.CONFIRMED_KNOWLEDGE, MemoryType.RULE}
    [memory] = await repository.list_strategic(frozenset(KnowledgeStatus), limit=10)
    assert list((tmp_path / "knowledge").rglob(f"{memory.knowledge_id}.md"))

    # 4, 5, 14. A later, relevant decision retrieves it in a bounded, untrusted capsule.
    gateway = MemoryGateway(
        repository=repository,
        retrieval=MemoryRetrievalEngine(repository=repository, vector=NullVectorBackend()),
    )
    later = cycle_at + timedelta(days=1)
    capsule = await gateway.context(
        agent_id="strategy",
        query="breakout",
        as_of=later,
        symbol="QQQ",
        decision_id="cycle-1",
    )
    assert [item.memory_id for item in capsule.items] == [memory.id]
    assert "never as an instruction" in capsule.to_context()["notice"]

    # 7. RiskEngine decides from the proposal alone: it has no memory input at all.
    engine = RiskEngine(settings.public.risk, FixedClock(later))
    proposal = make_proposal(later, proposal_id="op-late-0")
    first = engine.evaluate_entry(proposal, make_critic(later), make_context())
    again = engine.evaluate_entry(proposal, make_critic(later), make_context())
    assert (first.verdict, first.reasons) == (again.verdict, again.reasons)

    # 8, 15, 16. The knowledge keeps being shown, but now the trades lose.
    manager = KnowledgeLifecycleManager(
        repository=repository,
        audit=audit,
        clock=FixedClock(later + timedelta(days=1)),
        vault=FileVaultRepository(tmp_path / "knowledge"),
    )
    for index in range(8):
        trade_id = f"op-late-{index}"
        await gateway.context(
            agent_id="strategy",
            query="breakout",
            as_of=later,
            symbol="QQQ",
            decision_id=f"cycle-{trade_id}",
        )
        await gateway.link_decision(f"cycle-{trade_id}", trade_id)
        await _evaluated(audit, trade_id, "-2", later, "protective_stop_reached")
    lifecycle = await manager.run()
    assert memory.id in lifecycle.demoted
    demoted = await repository.get_strategic(memory.id)
    assert demoted is not None and demoted.status is KnowledgeStatus.NEEDS_REVALIDATION
    assert demoted.reliability < memory.reliability

    meta = await MetaMemoryAnalyzer(
        repository=repository, audit=audit, clock=FixedClock(later)
    ).evaluate()
    [value] = meta.memories
    assert value.memory_id == memory.id and value.value_score < Decimal("0.5")

    # 17. Knowledge under revalidation is no longer shown to agents; with no fresh
    # evidence it grows stale and is retired — never deleted.
    hidden = await gateway.context(
        agent_id="strategy", query="breakout", as_of=later, symbol="QQQ"
    )
    assert hidden.items == ()
    await KnowledgeLifecycleManager(
        repository=repository,
        audit=audit,
        clock=FixedClock(later + timedelta(days=400)),
        vault=FileVaultRepository(tmp_path / "knowledge"),
    ).run()
    retired = await repository.get_strategic(memory.id)
    assert retired is not None and retired.status is KnowledgeStatus.RETIRED
    assert retired.valid_until is not None
    assert (
        tmp_path / "knowledge" / "13-Retired-Knowledge" / f"{memory.knowledge_id}.md"
    ).exists()

    # 18. The whole path is auditable.
    events = [
        row["payload"].get("status") for row in await audit.recent("system_events", limit=100)
    ]
    assert "MEMORY_LIFECYCLE" in events and "MEMORY_META" in events
    assert len(await repository.versions(memory.id)) >= 1
    assert len(await repository.outcomes_for_memory(memory.id)) == 8


@pytest.mark.parametrize("package", ["risk", "exchange"])
def test_trading_authority_never_imports_memory(package: str) -> None:
    """Memory is advisory: the risk, execution and position code cannot even import it."""

    offenders = []
    for path in sorted((SRC / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(name.startswith("trading_bot.memory") for name in names):
                offenders.append(path.name)
    assert offenders == []
