"""Financial safety net: a position must always be closable and never oversized."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_context, make_critic, make_proposal
from test_pipeline_components import make_snapshot

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.config import load_settings
from trading_bot.config.models import RiskConfig
from trading_bot.core.clock import FixedClock
from trading_bot.core.orchestrator import MasterOrchestrator
from trading_bot.data.features import FeatureEngine
from trading_bot.db import AuditRepository, Database, TradingStateRepository
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.positions import DeterministicPositionManager, PositionState
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.strategies.trend_momentum import TrendMomentumStrategy

PRICES = tuple(Decimal(value) for value in ("100", "101", "102", "103", "105"))
MAX_HOLD = timedelta(minutes=240)


def _position(now: datetime, **updates: object) -> PositionState:
    values: dict[str, object] = {
        "position_id": "p1",
        "asset": "QQQ",
        "side": Side.BUY,
        "entry_price": Decimal("100"),
        "current_price": Decimal("100.5"),
        "quantity": Decimal("1"),
        "stop_price": Decimal("99"),
        "target_price": Decimal("102"),
        "opened_at": now,
        "protective_stop_active": True,
    }
    values.update(updates)
    return PositionState.model_validate(values)


def test_time_stop_closes_a_position_that_never_reaches_stop_or_target(now: datetime) -> None:
    later = FixedClock(now + MAX_HOLD)
    manager = DeterministicPositionManager(later, max_hold=MAX_HOLD)
    decision = manager.evaluate(_position(now), data_fresh=True)
    assert decision.action == "TIME_EXIT"
    assert decision.reasons == ("max_hold_time_exceeded",)


def test_position_inside_hold_window_is_kept(now: datetime) -> None:
    early = FixedClock(now + MAX_HOLD - timedelta(seconds=1))
    manager = DeterministicPositionManager(early, max_hold=MAX_HOLD)
    assert manager.evaluate(_position(now), data_fresh=True).action == "HOLD"


def test_stop_still_wins_over_time_stop(now: datetime) -> None:
    manager = DeterministicPositionManager(FixedClock(now + MAX_HOLD), max_hold=MAX_HOLD)
    hit = _position(now, current_price=Decimal("98"))
    assert manager.evaluate(hit, data_fresh=True).action == "EMERGENCY_EXIT"


def test_no_time_stop_is_applied_when_disabled(now: datetime) -> None:
    manager = DeterministicPositionManager(FixedClock(now + timedelta(days=30)))
    assert manager.evaluate(_position(now), data_fresh=True).action == "HOLD"


def test_default_config_enforces_a_time_stop(risk_config: RiskConfig) -> None:
    assert 1 <= risk_config.max_position_hold_minutes <= 1440


def test_entry_priced_away_from_market_is_denied(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    proposal = make_proposal(now)
    engine = RiskEngine(risk_config, clock)
    near = engine.evaluate_entry(
        proposal, make_critic(now), make_context(market_price=proposal.entry_price)
    )
    assert near.verdict == "ALLOW"
    far = engine.evaluate_entry(
        proposal,
        make_critic(now),
        make_context(market_price=proposal.entry_price * Decimal("0.98")),
    )
    assert far.verdict == "DENY"
    assert "entry_price_deviates_from_market" in far.reasons


def test_allow_states_what_it_authorizes_and_deny_authorizes_nothing(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    proposal = make_proposal(now)
    engine = RiskEngine(risk_config, clock)
    allow = engine.evaluate_entry(proposal, make_critic(now), make_context())
    assert allow.verdict == "ALLOW"
    assert allow.approved_asset == proposal.asset
    assert allow.approved_side == proposal.side
    assert allow.approved_quantity == proposal.quantity
    assert allow.approved_notional_usd == proposal.notional_usd
    deny = engine.evaluate_entry(
        proposal, make_critic(now), make_context(open_positions=risk_config.max_positions)
    )
    assert deny.verdict == "DENY"
    assert deny.approved_quantity is None
    assert deny.approved_notional_usd is None


def _orchestrator(database: Database, clock: FixedClock, risk_config: RiskConfig):
    settings = load_settings()
    state = TradingStateRepository(database)
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=AuditRepository(database),
        clock=clock,
        state_repository=state,
        max_position_hold=MAX_HOLD,
    )
    return orchestrator, state


class _Clock(FixedClock):
    def advance(self, delta: timedelta) -> None:
        self._value += delta


@pytest.mark.asyncio
async def test_open_position_is_force_closed_after_max_hold(
    tmp_path, risk_config: RiskConfig, now: datetime
) -> None:
    clock = _Clock(now)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'time-stop.db'}")
    await database.initialize()
    # One position at a time, so the sideways cycles cannot open a second one.
    risk_config = risk_config.model_copy(update={"max_positions": 1})
    orchestrator, state = _orchestrator(database, clock, risk_config)

    async def context():
        return await state.risk_context(
            mode=TradingMode.PAPER,
            starting_equity=Decimal("10000"),
            asset="QQQ",
            now=clock.now(),
        )

    opened = await orchestrator.run_cycle(make_snapshot(now), PRICES, await context())
    assert opened.status == "EXECUTED"
    assert len(await state.open_positions()) == 1
    horizon = timedelta(seconds=(await state.open_positions())[0]["max_hold_seconds"])
    limit = min(horizon, MAX_HOLD)

    # Price drifts sideways: neither stop nor target is touched.
    clock.advance(limit - timedelta(seconds=1))
    quiet = make_snapshot(clock.now())
    quiet_result = await orchestrator.run_cycle(quiet, PRICES, await context())
    assert quiet_result.status != "POSITION_EXITED"
    assert len(await state.open_positions()) == 1

    clock.advance(timedelta(seconds=1))
    closed = await orchestrator.run_cycle(make_snapshot(clock.now()), PRICES, await context())
    assert closed.status == "POSITION_EXITED"
    assert await state.open_positions() == []
    await database.close()


@pytest.mark.asyncio
async def test_protective_only_cycle_closes_positions_but_never_opens_new_ones(
    tmp_path, risk_config: RiskConfig, now: datetime
) -> None:
    clock = _Clock(now)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'orphan.db'}")
    await database.initialize()
    orchestrator, state = _orchestrator(database, clock, risk_config)
    ctx = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx)
    assert len(await state.open_positions()) == 1

    # Entry conditions are still perfect, but protective-only must not open anything new.
    clock.advance(MAX_HOLD)
    result = await orchestrator.run_cycle(
        make_snapshot(clock.now()), PRICES, ctx, protective_only=True
    )
    assert result.status == "POSITION_EXITED"
    assert await state.open_positions() == []
    again = await orchestrator.run_cycle(
        make_snapshot(clock.now()), PRICES, ctx, protective_only=True
    )
    assert again.status == "PROTECTION_ONLY"
    assert await state.open_positions() == []
    await database.close()


@pytest.mark.asyncio
async def test_operator_flatten_closes_positions_and_blocks_new_entries(
    tmp_path, risk_config: RiskConfig, now: datetime
) -> None:
    clock = _Clock(now)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'flatten.db'}")
    await database.initialize()
    orchestrator, state = _orchestrator(database, clock, risk_config)
    ctx = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    assert (await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx)).status == "EXECUTED"
    assert len(await state.open_positions()) == 1

    # Position is healthy (no stop/target/time exit) — only the operator asks to close it.
    closed = await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx, flatten=True)
    assert closed.status == "POSITION_EXITED"
    assert await state.open_positions() == []
    # Flat and still flattening: the perfect entry setup must NOT open a new position.
    again = await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx, flatten=True)
    assert again.status == "FLATTEN_PENDING"
    assert await state.open_positions() == []
    await database.close()


def test_proposal_horizon_tighter_than_global_cap_closes_first(now: datetime) -> None:
    position = _position(now, max_hold_seconds=900)
    def at(seconds: int) -> str:
        clock = FixedClock(now + timedelta(seconds=seconds))
        manager = DeterministicPositionManager(clock, max_hold=MAX_HOLD)
        return manager.evaluate(position, data_fresh=True).action

    assert at(900) == "TIME_EXIT"
    assert at(899) == "HOLD"


def test_global_cap_wins_when_proposal_horizon_is_longer(now: datetime) -> None:
    position = _position(now, max_hold_seconds=86_400)
    manager = DeterministicPositionManager(FixedClock(now + MAX_HOLD), max_hold=MAX_HOLD)
    assert manager.evaluate(position, data_fresh=True).action == "TIME_EXIT"


@pytest.mark.asyncio
async def test_executed_position_records_the_proposal_horizon(
    tmp_path, risk_config: RiskConfig, now: datetime
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'horizon.db'}")
    await database.initialize()
    orchestrator, state = _orchestrator(database, FixedClock(now), risk_config)
    ctx = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    assert (await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx)).status == "EXECUTED"
    position = (await state.open_positions())[0]
    assert isinstance(position["max_hold_seconds"], int) and position["max_hold_seconds"] > 0
    await database.close()


@pytest.mark.asyncio
async def test_flatten_waits_for_fresh_data_and_still_blocks_entries(
    tmp_path, risk_config: RiskConfig, now: datetime
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'flatten-stale.db'}")
    await database.initialize()
    orchestrator, state = _orchestrator(database, FixedClock(now), risk_config)
    ctx = await state.risk_context(
        mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now
    )
    await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx)
    stale = ctx.model_copy(update={"data_fresh": False})
    result = await orchestrator.run_cycle(make_snapshot(now), PRICES, stale, flatten=True)
    assert result.status == "FLATTEN_PENDING"  # no close at a stale price, no new entry
    assert len(await state.open_positions()) == 1
    fresh = await orchestrator.run_cycle(make_snapshot(now), PRICES, ctx, flatten=True)
    assert fresh.status == "POSITION_EXITED"
    await database.close()


@pytest.mark.asyncio
async def test_closed_trade_evaluation_compares_shadow_replay_with_real_fill(
    tmp_path, risk_config: RiskConfig, now: datetime
) -> None:
    from trading_bot.db.lifecycle import OperationLifecycleRepository

    clock = _Clock(now)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'shadow-vs-real.db'}")
    await database.initialize()
    settings = load_settings()
    state = TradingStateRepository(database)
    audit = AuditRepository(database)
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=audit,
        clock=clock,
        state_repository=state,
        lifecycle_repository=OperationLifecycleRepository(database),
    )

    async def ctx():
        return await state.risk_context(
            mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ",
            now=clock.now(),
        )

    assert (await orchestrator.run_cycle(make_snapshot(now), PRICES, await ctx())).status == (
        "EXECUTED"
    )
    target = Decimal(str((await state.open_positions())[0]["target_price"]))
    clock.advance(timedelta(minutes=5))
    await state.mark_to_market(asset="QQQ", current_price=target, now=clock.now())
    at_target = make_snapshot(clock.now()).model_copy(
        update={"bid": target - Decimal("0.01"), "ask": target, "last": target}
    )
    closed = await orchestrator.run_cycle(at_target, PRICES, await ctx())
    assert closed.status == "POSITION_EXITED"
    rows = await audit.recent("trade_evaluations", limit=5)
    evaluations = [
        row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        for row in rows
    ]
    comparison = evaluations[0]["shadow_comparison"]
    assert comparison["status"] == "compared"
    assert comparison["shadow_exit_reason"] == "target"
    real = Decimal(comparison["real_trade_pnl_usd"])
    shadow = Decimal(comparison["shadow_trade_pnl_usd"])
    assert Decimal(comparison["incremental_value_usd"]) == real - shadow
    assert real > 0 and shadow > 0
    await database.close()


@pytest.mark.asyncio
async def test_shadow_comparison_without_evidence_reports_and_never_blocks(
    tmp_path, now: datetime
) -> None:
    from trading_bot.core.post_close import PostCloseEvaluator
    from trading_bot.db.lifecycle import OperationLifecycleRepository

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'no-evidence.db'}")
    await database.initialize()
    evaluator = PostCloseEvaluator(
        lifecycle=OperationLifecycleRepository(database),
        state=TradingStateRepository(database),
        repository=AuditRepository(database),
        clock=FixedClock(now),
    )
    result = await evaluator._shadow_comparison("missing-op", "QQQ", Decimal("1"))
    assert result == {"status": "unavailable", "reason": "proposal_not_found"}
    await database.close()
