from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_bot.broker.lifecycle import OperationState
from trading_bot.db import Database, OperationLifecycleRepository
from trading_bot.schemas.common import TradingMode

NOW = datetime(2026, 9, 23, tzinfo=UTC)


async def _repository(tmp_path) -> OperationLifecycleRepository:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    await database.initialize()
    return OperationLifecycleRepository(database)


async def test_open_is_idempotent_and_logs_creation(tmp_path) -> None:
    repository = await _repository(tmp_path)
    for _ in range(2):
        state = await repository.open(
            operation_id="op-1",
            proposal_id="op-1",
            asset="QQQ",
            mode=TradingMode.PAPER,
            now=NOW,
        )
        assert state is OperationState.PROPOSED

    events = await repository.events("op-1")
    assert [event["to_state"] for event in events] == ["PROPOSED"]


async def test_advance_persists_ordered_append_only_log(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.open(
        operation_id="op-1", proposal_id="op-1", asset="QQQ", mode=TradingMode.PAPER, now=NOW
    )
    for target in (
        OperationState.CRITIC_REVIEWED,
        OperationState.RISK_APPROVED,
        OperationState.EXECUTION_PENDING,
    ):
        result = await repository.advance("op-1", target, reason="step", now=NOW)
        assert result.legal
    await repository.advance(
        "op-1",
        OperationState.SUBMITTED,
        reason="submitted",
        now=NOW,
        client_order_id="client-1",
    )

    row = await repository.get("op-1")
    assert row is not None
    assert row["state"] == "SUBMITTED"
    assert row["client_order_id"] == "client-1"
    events = await repository.events("op-1")
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
    assert events[-1]["from_state"] == "EXECUTION_PENDING"


async def test_illegal_transition_is_logged_and_fails_closed(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.open(
        operation_id="op-1", proposal_id="op-1", asset="QQQ", mode=TradingMode.PAPER, now=NOW
    )

    result = await repository.advance("op-1", OperationState.OPEN, reason="skip", now=NOW)

    assert not result.legal
    row = await repository.get("op-1")
    assert row is not None
    assert row["state"] == "RECOVERY_REQUIRED"
    last = (await repository.events("op-1"))[-1]
    assert last["legal"] is False
    assert last["requested_state"] == "OPEN"
    assert [op["id"] for op in await repository.active()] == ["op-1"]


async def test_terminal_operations_leave_active_set(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.open(
        operation_id="op-1", proposal_id="op-1", asset="QQQ", mode=TradingMode.PAPER, now=NOW
    )
    await repository.advance("op-1", OperationState.REJECTED, reason="risk", now=NOW)

    assert await repository.active() == []


async def test_unknown_operation_cannot_be_advanced(tmp_path) -> None:
    repository = await _repository(tmp_path)
    with pytest.raises(LookupError):
        await repository.advance("missing", OperationState.OPEN, reason="x", now=NOW)
