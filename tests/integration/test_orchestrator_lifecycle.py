from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.broker.base import SubmissionStateUnknown
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.models import OrderResult
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.config import load_settings
from trading_bot.core.orchestrator import MasterOrchestrator, default_paper_context
from trading_bot.data.features import FeatureEngine
from trading_bot.db import (
    AuditRepository,
    Database,
    OperationLifecycleRepository,
    TradingStateRepository,
)
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import TradingMode
from trading_bot.schemas.trading import ExecutionIntent, MarketSnapshot
from trading_bot.strategies.trend_momentum import TrendMomentumStrategy

PRICES = tuple(Decimal(value) for value in ("100", "101", "102", "103", "105"))


def _snapshot(now: datetime, last: Decimal = Decimal("105")) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="QQQ",
        bid=last - Decimal("0.01"),
        ask=last + Decimal("0.01"),
        last=last,
        session_volume=Decimal("25000"),
        session_dollar_volume=Decimal("25000000"),
        event_time=now,
        received_time=now,
        processed_time=now,
    )


class _TimeoutAdapter:
    """Simulates a submit that may have reached the exchange but was never confirmed."""

    def __init__(self) -> None:
        self.submits = 0

    async def submit(self, intent: ExecutionIntent) -> OrderResult:
        self.submits += 1
        raise SubmissionStateUnknown("timeout")

    async def lookup(self, client_order_id: str) -> OrderResult | None:
        return None


async def _setup(tmp_path, clock, risk_config, adapter=None):
    settings = load_settings()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle-orch.db'}")
    await database.initialize()
    lifecycle = OperationLifecycleRepository(database)
    state = TradingStateRepository(database)
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(adapter or SimulatedBroker(clock)),
        repository=AuditRepository(database),
        clock=clock,
        state_repository=state,
        lifecycle_repository=lifecycle,
    )
    return database, lifecycle, state, orchestrator


def _states(events: list[dict]) -> list[str]:
    return [str(event["to_state"]) for event in events]


@pytest.mark.asyncio
async def test_paper_entry_and_exit_walk_the_documented_lifecycle(
    tmp_path, clock, risk_config, now
) -> None:
    database, lifecycle, state, orchestrator = await _setup(tmp_path, clock, risk_config)
    context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )

    first = await orchestrator.run_cycle(_snapshot(now), PRICES, context)

    assert first.status == "EXECUTED"
    assert first.order is not None
    operation = await lifecycle.find_by_position(first.order.order_id)
    assert operation is not None
    assert operation["state"] == "OPEN"
    assert operation["client_order_id"] == first.order.client_order_id

    position = (await state.open_positions())[0]
    target = Decimal(str(position["target_price"]))
    await state.mark_to_market(asset="QQQ", current_price=target, now=now)
    next_context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    second = await orchestrator.run_cycle(_snapshot(now, target), PRICES, next_context)

    assert second.status == "POSITION_EXITED"
    events = await lifecycle.events(str(operation["id"]))
    assert _states(events) == [
        "PROPOSED",
        "CRITIC_REVIEWED",
        "RISK_APPROVED",
        "EXECUTION_PENDING",
        "SUBMITTED",
        "OPEN",
        "EXIT_PENDING",
        "CLOSING",
        "CLOSED",
        "RECONCILED",
        "EVALUATED",
    ]
    assert all(event["legal"] for event in events)
    assert await lifecycle.active() == []

    evaluation = (await AuditRepository(database).recent("trade_evaluations"))[0]["payload"]
    assert evaluation["trade_id"] == operation["id"]
    assert Decimal(evaluation["realized_net_pnl"]) > 0
    assert Decimal(evaluation["fees_usd"]) > 0
    assert Decimal(evaluation["slippage_usd"]) > 0
    # Marked at the target before the exit, so the best excursion is positive.
    assert Decimal(evaluation["mfe_usd"]) > 0
    assert Decimal(evaluation["mae_usd"]) <= 0
    assert evaluation["signal_correct"] is True
    assert evaluation["thesis_valid"] is True
    await database.close()


@pytest.mark.asyncio
async def test_partial_fills_keep_the_remainder_open_and_tracked(
    tmp_path, clock, risk_config, now
) -> None:
    database, lifecycle, state, orchestrator = await _setup(
        tmp_path,
        clock,
        risk_config,
        SimulatedBroker(clock, fill_fraction=Decimal("0.5")),
    )
    context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )

    first = await orchestrator.run_cycle(_snapshot(now), PRICES, context)

    assert first.order is not None
    assert first.order.status == "PARTIALLY_FILLED"
    operation = await lifecycle.find_by_position(first.order.order_id)
    assert operation is not None
    assert operation["state"] == "PARTIALLY_FILLED"

    position = (await state.open_positions())[0]
    stop = Decimal(str(position["stop_price"]))
    await state.mark_to_market(asset="QQQ", current_price=stop, now=now)
    next_context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    second = await orchestrator.run_cycle(_snapshot(now, stop), PRICES, next_context)

    assert second.status == "POSITION_EXITED"
    # The exit (also half-filled by this adapter) leaves the remainder open.
    refreshed = await lifecycle.get(str(operation["id"]))
    assert refreshed is not None
    assert refreshed["state"] == "OPEN"
    remaining = (await state.open_positions())[0]
    assert Decimal(str(remaining["mae_usd"])) < 0
    await database.close()


@pytest.mark.asyncio
async def test_closed_position_with_missing_fills_enters_safe_mode(
    tmp_path, clock, risk_config, now
) -> None:
    database, lifecycle, state, orchestrator = await _setup(tmp_path, clock, risk_config)
    context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    first = await orchestrator.run_cycle(_snapshot(now), PRICES, context)
    assert first.order is not None

    async def _lost_fills(position_id: str) -> tuple[str, ...]:
        return ("fill_quantity_mismatch",)

    state.closed_position_mismatches = _lost_fills  # type: ignore[method-assign]
    position = (await state.open_positions())[0]
    target = Decimal(str(position["target_price"]))
    await state.mark_to_market(asset="QQQ", current_price=target, now=now)
    next_context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    await orchestrator.run_cycle(_snapshot(now, target), PRICES, next_context)

    operation = await lifecycle.find_by_position(first.order.order_id)
    assert operation is not None
    assert operation["state"] == "SAFE_MODE"
    events = await AuditRepository(database).recent("system_events")
    assert any(
        row["payload"].get("status") == "RECONCILIATION_MISMATCH" and row["payload"]["safe_mode"]
        for row in events
    )
    assert not await AuditRepository(database).recent("trade_evaluations")
    await database.close()


@pytest.mark.asyncio
async def test_risk_rejection_is_terminal(tmp_path, clock, risk_config, now) -> None:
    database, lifecycle, _, orchestrator = await _setup(tmp_path, clock, risk_config)

    # A loss cooldown is a deterministic RiskEngine denial.
    result = await orchestrator.run_cycle(
        _snapshot(now),
        PRICES,
        default_paper_context(Decimal("100")).model_copy(update={"cooldown_active": True}),
    )

    assert result.status == "REJECTED"
    assert result.risk_decision is not None
    operation = await lifecycle.get(result.risk_decision.proposal_id)
    assert operation is not None
    assert operation["state"] == "REJECTED"
    assert await lifecycle.active() == []
    await database.close()


@pytest.mark.asyncio
async def test_submission_timeout_parks_operation_in_unknown_without_retry(
    tmp_path, clock, risk_config, now
) -> None:
    adapter = _TimeoutAdapter()
    database, lifecycle, state, orchestrator = await _setup(
        tmp_path, clock, risk_config, adapter
    )

    result = await orchestrator.run_cycle(
        _snapshot(now), PRICES, default_paper_context(Decimal("10000"))
    )

    assert result.status == "EXECUTION_UNKNOWN"
    assert adapter.submits == 1
    active = await lifecycle.active()
    assert [row["state"] for row in active] == ["UNKNOWN"]
    # A timeout is never treated as a fill nor as a rejection.
    assert await state.open_positions() == []
    events = await AuditRepository(database).recent("system_events")
    assert events[0]["payload"]["status"] == "EXECUTION_UNKNOWN"
    await database.close()
