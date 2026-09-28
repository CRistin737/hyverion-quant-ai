from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_bot.broker.lifecycle import OperationState
from trading_bot.broker.models import Fill, OrderResult
from trading_bot.core.clock import FixedClock
from trading_bot.core.operator_resolution import (
    OperatorResolutionError,
    OperatorResolutionService,
)
from trading_bot.db import (
    AuditRepository,
    Database,
    OperationLifecycleRepository,
    TradingStateRepository,
)
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent

NOW = datetime(2026, 9, 23, tzinfo=UTC)
S = OperationState


async def _env(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'operator.db'}")
    await database.initialize()
    lifecycle = OperationLifecycleRepository(database)
    state = TradingStateRepository(database)
    audit = AuditRepository(database)
    service = OperatorResolutionService(
        lifecycle=lifecycle, state=state, repository=audit, clock=FixedClock(NOW)
    )
    return database, lifecycle, state, audit, service


async def _unresolved(lifecycle, *, position_id: str | None = None) -> None:
    await lifecycle.open(
        operation_id="op-1", proposal_id="op-1", asset="QQQ", mode=TradingMode.PAPER, now=NOW
    )
    path = [S.CRITIC_REVIEWED, S.RISK_APPROVED, S.EXECUTION_PENDING, S.SUBMITTED]
    if position_id:
        path.append(S.OPEN)
    path.append(S.RECOVERY_REQUIRED)
    for target in path:
        await lifecycle.advance(
            "op-1",
            target,
            reason="setup",
            now=NOW,
            position_id=position_id if target is S.OPEN else None,
        )


async def _open_position(state: TradingStateRepository) -> str:
    await state.ensure_account(equity=Decimal("10000"), now=NOW)
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
                fill_id="f-1",
                price=Decimal("100"),
                quantity=Decimal("1"),
                fee_usd=Decimal("0.1"),
                filled_at=NOW,
            ),
        ),
        protective_stop_active=True,
        created_at=NOW,
    )
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
    await state.record_execution(intent=intent, order=order, now=NOW)
    return "pos-1"


async def test_operator_confirms_no_order_exists(tmp_path) -> None:
    database, lifecycle, _, audit, service = await _env(tmp_path)
    await _unresolved(lifecycle)

    assert await service.resolve("op-1", S.REJECTED, "  Venue shows no order  ") is S.REJECTED
    assert await lifecycle.count_unresolved() == 0
    last = (await lifecycle.events("op-1"))[-1]
    assert last["reason"] == "operator:Venue shows no order"
    event = (await audit.recent("system_events"))[0]["payload"]
    assert event["status"] == "OPERATOR_RESOLUTION"
    await database.close()


async def test_operator_cannot_erase_a_recorded_position(tmp_path) -> None:
    database, lifecycle, state, _, service = await _env(tmp_path)
    position_id = await _open_position(state)
    await _unresolved(lifecycle, position_id=position_id)

    with pytest.raises(OperatorResolutionError):
        await service.resolve("op-1", S.REJECTED, "try to hide it")
    with pytest.raises(OperatorResolutionError):
        await service.resolve("op-1", S.CLOSED, "not actually closed")
    assert await service.resolve("op-1", S.OPEN, "position verified and protected") is S.OPEN
    await database.close()


async def test_open_requires_a_position_and_input_is_validated(tmp_path) -> None:
    database, lifecycle, _, _, service = await _env(tmp_path)
    await _unresolved(lifecycle)

    with pytest.raises(OperatorResolutionError):
        await service.resolve("op-1", S.OPEN, "no position recorded")
    with pytest.raises(OperatorResolutionError):
        await service.resolve("op-1", S.EVALUATED, "skip ahead")
    with pytest.raises(OperatorResolutionError):
        await service.resolve("op-1", S.REJECTED, "x")
    with pytest.raises(LookupError):
        await service.resolve("missing", S.REJECTED, "valid reason")
    await database.close()


async def test_resolved_operations_cannot_be_touched(tmp_path) -> None:
    database, lifecycle, _, _, service = await _env(tmp_path)
    await _unresolved(lifecycle)
    await service.resolve("op-1", S.REJECTED, "venue shows no order")

    with pytest.raises(OperatorResolutionError):
        await service.resolve("op-1", S.CANCELED, "second attempt")
    await database.close()
