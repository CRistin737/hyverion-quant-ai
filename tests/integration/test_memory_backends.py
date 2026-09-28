"""Memory backends against real services when available.

The Redis and PostgreSQL tests run only when ``REDIS_URL`` /
``HYVERION_TEST_POSTGRES_URL`` point at the local Docker ``memory`` profile;
otherwise they are skipped. The rebuild test always runs on SQLite.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from trading_bot.broker.lifecycle import OperationState
from trading_bot.broker.models import Fill, OrderResult
from trading_bot.core.clock import FixedClock
from trading_bot.db import Database, OperationLifecycleRepository, TradingStateRepository
from trading_bot.memory import MemoryType, SqlMemoryRepository, StrategicMemory
from trading_bot.memory.maintenance import WorkingMemoryRebuilder
from trading_bot.memory.working import (
    InMemoryWorkingMemoryAdapter,
    WorkingMemoryUnavailable,
    build_working_memory,
)
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent

NOW = datetime(2026, 9, 23, tzinfo=UTC)
REDIS_URL = os.getenv("REDIS_URL")
POSTGRES_URL = os.getenv("HYVERION_TEST_POSTGRES_URL")


async def _seed_open_position(database: Database) -> None:
    state = TradingStateRepository(database)
    lifecycle = OperationLifecycleRepository(database)
    await state.ensure_account(equity=Decimal("10000"), now=NOW)
    intent = ExecutionIntent(
        intent_id="i-1",
        decision_id="d-1",
        proposal_id="op-1",
        client_order_id="hyverion-op-1",
        mode=TradingMode.PAPER,
        asset="QQQ",
        side=Side.BUY,
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        created_at=NOW,
    )
    order = OrderResult(
        order_id="pos-1",
        client_order_id="hyverion-op-1",
        mode=TradingMode.PAPER,
        asset="QQQ",
        side=Side.BUY,
        requested_quantity=Decimal("1"),
        filled_quantity=Decimal("1"),
        status="FILLED",
        fills=(
            Fill(
                fill_id=f"f-{uuid4().hex[:8]}",
                price=Decimal("100"),
                quantity=Decimal("1"),
                fee_usd=Decimal("0.1"),
                filled_at=NOW,
            ),
        ),
        protective_stop_active=True,
        created_at=NOW,
    )
    await state.record_execution(intent=intent, order=order, now=NOW)
    await lifecycle.open(
        operation_id="op-1", proposal_id="op-1", asset="QQQ", mode=TradingMode.PAPER, now=NOW
    )
    for target in (
        OperationState.CRITIC_REVIEWED,
        OperationState.RISK_APPROVED,
        OperationState.EXECUTION_PENDING,
        OperationState.SUBMITTED,
    ):
        await lifecycle.advance("op-1", target, reason="setup", now=NOW)
    await lifecycle.advance(
        "op-1", OperationState.OPEN, reason="setup", now=NOW, position_id="pos-1"
    )


async def test_working_memory_is_rebuilt_from_the_database(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'rebuild.db'}")
    await database.initialize()
    await _seed_open_position(database)
    backend = InMemoryWorkingMemoryAdapter(FixedClock(NOW))

    summary = await WorkingMemoryRebuilder(
        backend=backend,
        state=TradingStateRepository(database),
        lifecycle=OperationLifecycleRepository(database),
        clock=FixedClock(NOW),
    ).rebuild()

    assert summary["open_position_ids"] == ["pos-1"]
    assert summary["unresolved_operations"] == 0
    position = await backend.get("position:pos-1")
    assert position is not None
    assert position["operation_id"] == "op-1"
    assert position["entry_price"] == "100"
    assert position["protective_stop_active"] is True
    assert await backend.get("session:current") == summary
    await database.close()


@pytest.mark.skipif(not REDIS_URL, reason="REDIS_URL not set (docker memory profile)")
async def test_redis_working_memory_round_trip_and_ttl() -> None:
    backend = build_working_memory(
        environment="development",
        backend="redis",
        clock=FixedClock(NOW),
        redis_url=REDIS_URL,
        prefix=f"test-{uuid4().hex[:8]}",
    )
    await backend.set("risk:current", {"level": 0, "reasons": ["ok"]}, ttl=timedelta(seconds=30))
    assert await backend.get("risk:current") == {"level": 0, "reasons": ["ok"]}
    await backend.delete("risk:current")
    assert await backend.get("risk:current") is None
    with pytest.raises(ValueError):
        await backend.set("risk:current", {"bad": Decimal("1")})


async def test_unreachable_redis_is_explicitly_unavailable() -> None:
    pytest.importorskip("redis")
    backend = build_working_memory(
        environment="development",
        backend="redis",
        clock=FixedClock(NOW),
        redis_url="redis://127.0.0.1:1/0",
    )
    with pytest.raises(WorkingMemoryUnavailable):
        await backend.get("risk:current")


@pytest.mark.skipif(not POSTGRES_URL, reason="HYVERION_TEST_POSTGRES_URL not set")
async def test_postgres_runs_the_same_schema_and_memory_repository() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    await database.initialize()
    suffix = uuid4().hex[:8]
    repository = SqlMemoryRepository(database)
    memory = StrategicMemory(
        id=f"pg-{suffix}",
        knowledge_id=f"KNOW-pg-{suffix}",
        title="Postgres round trip",
        summary="Portable schema works on PostgreSQL.",
        category=MemoryType.PATTERN,
        symbol="QQQ",
        confidence=Decimal("0.5"),
        reliability=Decimal("0.5"),
        importance=Decimal("0.5"),
        valid_from=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    await repository.create_strategic(memory, content="body", created_by="test", change_reason="x")
    stored = await repository.get_strategic(memory.id)
    assert stored is not None
    assert stored.created_at == NOW
    assert stored.confidence == Decimal("0.5")
    # The Docker database is shared across runs, so look the row up by its unique id
    # instead of relying on a top-N ranking.
    found = await repository.browse_strategic(text=suffix, symbol="QQQ")
    assert [item.id for item in found] == [memory.id]
    before = await repository.search_strategic(
        as_of=NOW - timedelta(seconds=1), symbol="QQQ", limit=50
    )
    assert memory.id not in {item.id for item in before}  # time-aware on PostgreSQL too

    lifecycle = OperationLifecycleRepository(database)
    operation_id = f"op-pg-{suffix}"
    await lifecycle.open(
        operation_id=operation_id,
        proposal_id=operation_id,
        asset="QQQ",
        mode=TradingMode.PAPER,
        now=NOW,
    )
    result = await lifecycle.advance(operation_id, OperationState.OPEN, reason="skip", now=NOW)
    assert result.to_state is OperationState.RECOVERY_REQUIRED
    await database.close()
