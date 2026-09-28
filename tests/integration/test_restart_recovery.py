from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.lifecycle import OperationState
from trading_bot.broker.models import Fill, OrderResult
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.config import load_settings
from trading_bot.core.clock import FixedClock
from trading_bot.core.orchestrator import MasterOrchestrator
from trading_bot.core.recovery import OperationRecoveryService
from trading_bot.data.features import FeatureEngine
from trading_bot.db import (
    AuditRepository,
    Database,
    OperationLifecycleRepository,
    TradingStateRepository,
)
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent, MarketSnapshot
from trading_bot.strategies.trend_momentum import TrendMomentumStrategy

# Wednesday 11:00 New York time: inside the regular session.
NOW = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
S = OperationState


class _VenueStub:
    """Read-only venue: returns whatever orders were placed on it before the restart."""

    def __init__(
        self, orders: dict[str, OrderResult] | None = None, *, simulated: bool = True
    ) -> None:
        self.orders = orders or {}
        self.submits = 0
        # True models the in-process simulator; False an external paper broker.
        self.simulated = simulated

    async def submit(self, intent: ExecutionIntent) -> OrderResult:
        self.submits += 1
        raise AssertionError("recovery must never submit")

    async def lookup(self, client_order_id: str) -> OrderResult | None:
        return self.orders.get(client_order_id)


def _filled(client_order_id: str) -> OrderResult:
    return OrderResult(
        order_id="venue-order-1",
        client_order_id=client_order_id,
        mode=TradingMode.LIVE,
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


async def _env(tmp_path, venue: _VenueStub | None = None):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    await database.initialize()
    lifecycle = OperationLifecycleRepository(database)
    state = TradingStateRepository(database)
    audit = AuditRepository(database)
    venue = venue or _VenueStub()
    service = OperationRecoveryService(
        lifecycle=lifecycle,
        state=state,
        execution_engine=ExecutionEngine(venue),
        repository=audit,
        clock=FixedClock(NOW),
    )
    return database, lifecycle, service, audit


async def _operation_at(
    lifecycle: OperationLifecycleRepository,
    path: tuple[OperationState, ...],
    *,
    operation_id: str = "op-1",
    mode: TradingMode = TradingMode.PAPER,
    client_order_id: str | None = "client-1",
    position_id: str | None = None,
) -> None:
    await lifecycle.open(
        operation_id=operation_id,
        proposal_id=operation_id,
        asset="QQQ",
        mode=mode,
        now=NOW,
    )
    for target in path:
        result = await lifecycle.advance(
            operation_id,
            target,
            reason="setup",
            now=NOW,
            client_order_id=client_order_id if target is S.EXECUTION_PENDING else None,
            position_id=position_id if target in {S.OPEN, S.PARTIALLY_FILLED} else None,
        )
        assert result.legal


TO_PENDING = (S.CRITIC_REVIEWED, S.RISK_APPROVED, S.EXECUTION_PENDING)


async def test_pre_submission_operations_are_rejected(tmp_path) -> None:
    database, lifecycle, service, _ = await _env(tmp_path)
    await _operation_at(lifecycle, (S.CRITIC_REVIEWED,))

    report = await service.recover()

    assert report.safe
    assert (await lifecycle.get("op-1"))["state"] == "REJECTED"
    await database.close()


async def test_simulated_order_absent_after_restart_is_rejected(tmp_path) -> None:
    database, lifecycle, service, audit = await _env(tmp_path)
    await _operation_at(lifecycle, TO_PENDING)

    report = await service.recover()

    assert report.safe
    assert report.transitions == ("op-1:EXECUTION_PENDING->REJECTED",)
    event = (await audit.recent("system_events"))[0]["payload"]
    assert event["status"] == "RESTART_RECOVERY_OK"
    await database.close()


async def test_external_paper_broker_missing_order_requires_operator(tmp_path) -> None:
    """Alpaca Paper is a real venue: a missing order is never assumed unfilled."""

    database, lifecycle, service, _audit = await _env(tmp_path, _VenueStub(simulated=False))
    await _operation_at(lifecycle, TO_PENDING)

    report = await service.recover()

    assert not report.safe
    assert (await lifecycle.get("op-1"))["state"] == "RECOVERY_REQUIRED"
    await database.close()


async def test_real_venue_timeout_without_order_requires_operator(tmp_path) -> None:
    database, lifecycle, service, audit = await _env(tmp_path)
    await _operation_at(lifecycle, (*TO_PENDING, S.UNKNOWN), mode=TradingMode.LIVE)

    report = await service.recover()

    assert not report.safe
    assert report.unresolved_operation_ids == ("op-1",)
    assert (await lifecycle.get("op-1"))["state"] == "RECOVERY_REQUIRED"
    assert await lifecycle.count_unresolved() == 1
    event = (await audit.recent("system_events"))[0]["payload"]
    assert event["status"] == "RESTART_RECOVERY_REQUIRED"
    assert event["safe_mode"] is True
    await database.close()


async def test_venue_fill_without_local_projection_is_not_invented(tmp_path) -> None:
    venue = _VenueStub({"client-1": _filled("client-1")})
    database, lifecycle, service, _ = await _env(tmp_path, venue)
    await _operation_at(lifecycle, TO_PENDING, mode=TradingMode.LIVE)

    report = await service.recover()

    assert not report.safe
    events = await lifecycle.events("op-1")
    assert [event["to_state"] for event in events][-2:] == ["SUBMITTED", "RECOVERY_REQUIRED"]
    assert events[-1]["reason"] == "fill_without_local_projection"
    assert venue.submits == 0
    await database.close()


async def test_open_operation_without_position_projection_escalates(tmp_path) -> None:
    database, lifecycle, service, _ = await _env(tmp_path)
    await _operation_at(lifecycle, (*TO_PENDING, S.SUBMITTED, S.OPEN), position_id="missing")

    report = await service.recover()

    assert report.unresolved_operation_ids == ("op-1",)
    await database.close()


@pytest.mark.asyncio
async def test_real_paper_cycle_survives_restart_and_unresolved_blocks_entries(
    tmp_path, risk_config
) -> None:
    clock = FixedClock(NOW)
    settings = load_settings()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}")
    await database.initialize()
    lifecycle = OperationLifecycleRepository(database)
    state = TradingStateRepository(database)
    audit = AuditRepository(database)

    def orchestrator() -> MasterOrchestrator:
        return MasterOrchestrator(
            feature_engine=FeatureEngine(),
            strategy=TrendMomentumStrategy(
                settings.public.strategies.signal_weights, "v1", clock
            ),
            critic=DeterministicCritic(clock, risk_config.max_spread_bps),
            risk_engine=RiskEngine(risk_config, clock),
            execution_engine=ExecutionEngine(SimulatedBroker(clock)),
            repository=audit,
            clock=clock,
            state_repository=state,
            lifecycle_repository=lifecycle,
        )

    snapshot = MarketSnapshot(
        symbol="QQQ",
        bid=Decimal("104.99"),
        ask=Decimal("105.01"),
        last=Decimal("105"),
        session_volume=Decimal("25000"),
        session_dollar_volume=Decimal("25000000"),
        event_time=NOW,
        received_time=NOW,
        processed_time=NOW,
    )
    prices = tuple(Decimal(value) for value in ("100", "101", "102", "103", "105"))
    context = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=NOW
    )
    first = await orchestrator().run_cycle(snapshot, prices, context)
    assert first.status == "EXECUTED"

    # Restart: the in-memory paper venue is gone, but the projection survives.
    service = OperationRecoveryService(
        lifecycle=lifecycle,
        state=state,
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=audit,
        clock=clock,
    )
    report = await service.recover()
    assert report.safe
    assert [row["state"] for row in await lifecycle.active()] == ["OPEN"]

    # A different, unresolved operation must block the next entry.
    await _operation_at(
        lifecycle, (*TO_PENDING, S.UNKNOWN), operation_id="op-stuck", mode=TradingMode.PAPER
    )
    blocked = await orchestrator().run_cycle(snapshot, prices, context)
    assert blocked.status == "REJECTED"
    assert blocked.risk_decision is not None
    assert "unresolved_operation_requires_recovery" in blocked.risk_decision.reasons
    await database.close()


class _RestingExitVenue:
    """Real-venue stub: exit orders fill half and leave the remainder resting (GTC)."""

    def __init__(self) -> None:
        self.orders: dict[str, OrderResult] = {}
        self.submits = 0

    async def submit(self, intent: ExecutionIntent) -> OrderResult:
        self.submits += 1
        half = intent.quantity / 2
        order = OrderResult(
            order_id=f"venue-{self.submits}",
            client_order_id=intent.client_order_id,
            mode=intent.mode,
            asset=intent.asset,
            side=intent.side,
            requested_quantity=intent.quantity,
            filled_quantity=half,
            status="PARTIALLY_FILLED",
            fills=(
                Fill(
                    fill_id=f"fill-{self.submits}",
                    price=intent.limit_price,
                    quantity=half,
                    fee_usd=Decimal("0.01"),
                    filled_at=NOW,
                ),
            ),
            protective_stop_active=True,
            created_at=NOW,
        )
        self.orders[intent.client_order_id] = order
        return order

    async def lookup(self, client_order_id: str) -> OrderResult | None:
        return self.orders.get(client_order_id)


async def _live_position(tmp_path, venue, risk_config):
    """Seed an open LIVE-mode position whose operation is OPEN."""

    clock = FixedClock(NOW)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'live-exit.db'}")
    await database.initialize()
    lifecycle = OperationLifecycleRepository(database)
    state = TradingStateRepository(database)
    audit = AuditRepository(database)
    await state.ensure_account(equity=Decimal("10000"), now=NOW)
    entry_intent = ExecutionIntent(
        intent_id="intent-live",
        decision_id="decision-live",
        proposal_id="op-live",
        client_order_id="hyverion-op-live",
        mode=TradingMode.LIVE,
        asset="QQQ",
        side=Side.BUY,
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        created_at=NOW,
    )
    entry = _filled("hyverion-op-live")
    await state.record_execution(intent=entry_intent, order=entry, now=NOW)
    await _operation_at(
        lifecycle,
        (*TO_PENDING, S.SUBMITTED, S.OPEN),
        operation_id="op-live",
        mode=TradingMode.LIVE,
        client_order_id="hyverion-op-live",
        position_id=entry.order_id,
    )
    settings = load_settings()
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(venue),
        repository=audit,
        clock=clock,
        state_repository=state,
        lifecycle_repository=lifecycle,
    )
    await state.mark_to_market(asset="QQQ", current_price=Decimal("99"), now=NOW)
    context = await state.risk_context(
        mode=TradingMode.LIVE, starting_equity=Decimal("10000"), asset="QQQ", now=NOW
    )
    stop_snapshot = MarketSnapshot(
        symbol="QQQ",
        bid=Decimal("98.99"),
        ask=Decimal("99.01"),
        last=Decimal("99"),
        session_volume=Decimal("25000"),
        session_dollar_volume=Decimal("25000000"),
        event_time=NOW,
        received_time=NOW,
        processed_time=NOW,
    )
    return database, lifecycle, orchestrator, context, stop_snapshot


@pytest.mark.asyncio
async def test_working_exit_order_is_never_duplicated(tmp_path, risk_config) -> None:
    venue = _RestingExitVenue()
    database, lifecycle, orchestrator, context, stop = await _live_position(
        tmp_path, venue, risk_config
    )
    prices = tuple(Decimal(value) for value in ("101", "100", "99.5", "99.2", "99"))

    first = await orchestrator.run_cycle(stop, prices, context)
    assert first.status == "POSITION_EXITED"
    assert (await lifecycle.get("op-live"))["state"] == "CLOSING"

    # Stop is still breached, but the first exit order is still resting.
    await orchestrator.run_cycle(stop, prices, context)
    assert venue.submits == 1

    # The venue finishes the order; its extra fills are not projected locally.
    order = next(iter(venue.orders.values()))
    venue.orders[order.client_order_id] = order.model_copy(update={"status": "FILLED"})
    await orchestrator.run_cycle(stop, prices, context)

    assert venue.submits == 1
    assert (await lifecycle.get("op-live"))["state"] == "RECOVERY_REQUIRED"
    await database.close()


@pytest.mark.asyncio
async def test_real_venue_exit_is_proven_by_lookup_on_restart(tmp_path, risk_config) -> None:
    venue = _RestingExitVenue()
    database, lifecycle, orchestrator, context, stop = await _live_position(
        tmp_path, venue, risk_config
    )
    prices = tuple(Decimal(value) for value in ("101", "100", "99.5", "99.2", "99"))
    await orchestrator.run_cycle(stop, prices, context)
    state = TradingStateRepository(database)
    audit = AuditRepository(database)

    def service(engine_venue) -> OperationRecoveryService:
        return OperationRecoveryService(
            lifecycle=lifecycle,
            state=state,
            execution_engine=ExecutionEngine(engine_venue),
            repository=audit,
            clock=FixedClock(NOW),
        )

    # Venue still shows the exit working: keep the state.
    assert (await service(venue).recover()).safe
    assert (await lifecycle.get("op-live"))["state"] == "CLOSING"

    # A venue that cannot find it: escalate instead of trusting the local projection.
    report = await service(_VenueStub()).recover()
    assert report.unresolved_operation_ids == ("op-live",)
    await database.close()
