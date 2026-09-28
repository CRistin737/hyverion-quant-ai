from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.reconciliation import ReconciliationSnapshot
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.core.clock import FixedClock
from trading_bot.db import Database, TradingStateRepository
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent, PositionDecision, RiskDecision


def _intent(now: datetime) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id="intent-state",
        decision_id="decision-state",
        proposal_id="proposal-state",
        client_order_id="client-state",
        mode=TradingMode.PAPER,
        asset="QQQ",
        side=Side.BUY,
        quantity=Decimal("0.1"),
        limit_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        created_at=now,
    )


def _decision(now: datetime) -> RiskDecision:
    return RiskDecision.model_validate(
        {
            "decision_id": "decision-state",
            "proposal_id": "proposal-state",
            "verdict": "ALLOW",
            "reasons": [],
            "level": 0,
            "risk_multiplier": "1",
            "base_risk_usd": "1",
            "allowed_risk_usd": "1",
            "candidate_worst_case_loss_usd": "1",
            "protected_profit_floor_usd": "0",
            "live_trading_allowed": False,
            "shadow_trading": True,
            "decided_at": now,
            "approved_asset": "QQQ",
            "approved_side": "buy",
            "approved_quantity": "0.1",
            "approved_notional_usd": "10",
        }
    )


async def test_state_rebuilds_risk_context_and_deduplicates_execution(tmp_path) -> None:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.initialize()
    repository = TradingStateRepository(database)
    context = await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    assert context.equity == Decimal("100")
    assert context.open_positions == 0

    result = await ExecutionEngine(SimulatedBroker(FixedClock(now))).execute(
        _intent(now), _decision(now)
    )
    await repository.record_execution(intent=_intent(now), order=result, now=now)
    await repository.record_execution(intent=_intent(now), order=result, now=now)
    await repository.mark_to_market(
        asset="QQQ",
        current_price=Decimal("101"),
        now=now,
    )

    rebuilt = await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    assert rebuilt.open_positions == 1
    assert rebuilt.current_exposure_usd > 0
    assert rebuilt.fees_today > 0
    assert rebuilt.unrealized_pnl > 0
    assert rebuilt.intraday_peak_total_pnl > 0
    assert rebuilt.account_high_water_mark > Decimal("100")
    protection = await repository.protective_stop_recovery()
    assert protection.ok is True
    assert protection.protected_positions == 1
    await database.close()


async def test_deterministic_exit_realizes_pnl_and_closes_position(
    tmp_path, risk_config, now
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'exit.db'}")
    await database.initialize()
    repository = TradingStateRepository(database)
    entry_intent = _intent(now)
    paper = SimulatedBroker(FixedClock(now))
    execution = ExecutionEngine(paper)
    entry_order = await execution.execute(entry_intent, _decision(now))
    await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    await repository.record_execution(intent=entry_intent, order=entry_order, now=now)
    position = (await repository.open_positions())[0]
    exit_decision = RiskEngine(risk_config, FixedClock(now)).evaluate_exit(
        PositionDecision(
            position_id=str(position["position_id"]),
            action="TAKE_PROFIT",
            reasons=("target_reached",),
            created_at=now,
        ),
        await repository.risk_context(
            mode=TradingMode.PAPER,
            starting_equity=Decimal("100"),
            asset="QQQ",
            now=now,
        ),
    )
    exit_intent = ExecutionIntent(
        intent_id="exit-intent",
        decision_id=exit_decision.decision_id,
        proposal_id=str(position["position_id"]),
        client_order_id="exit-client-state",
        mode=TradingMode.PAPER,
        asset="QQQ",
        side=Side.SELL,
        quantity=Decimal(str(position["quantity"])),
        limit_price=Decimal("102"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        created_at=now,
        reduce_only=True,
        position_id=str(position["position_id"]),
        exit_reason="target_reached",
    )
    exit_order = await execution.execute(exit_intent, exit_decision)
    await repository.record_execution(intent=exit_intent, order=exit_order, now=now)
    await repository.record_execution(intent=exit_intent, order=exit_order, now=now)

    rebuilt = await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    assert rebuilt.open_positions == 0
    assert rebuilt.realized_net_pnl_today > 0
    assert rebuilt.losing_streak == 0
    # A restart can replay the already-filled entry order after its position
    # has closed. Closed projections must still participate in idempotency.
    await repository.record_execution(intent=entry_intent, order=entry_order, now=now)
    assert await repository.open_positions() == []
    await database.close()


async def test_session_stop_latch_survives_context_rebuild(tmp_path) -> None:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'session.db'}")
    await database.initialize()
    repository = TradingStateRepository(database)
    await repository.risk_context(
        mode=TradingMode.LIVE,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    await repository.persist_session_decision(
        action="STOP_LIVE_FOR_DAY",
        reasons=("maximum_losing_streak_reached",),
        now=now,
    )
    rebuilt = await repository.risk_context(
        mode=TradingMode.LIVE,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    assert rebuilt.live_stopped is True
    assert rebuilt.session_stop_reason == "maximum_losing_streak_reached"
    await database.close()


async def test_reconciliation_projects_local_state_and_enters_safe_mode_on_mismatch(
    tmp_path,
) -> None:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'reconciliation.db'}")
    await database.initialize()
    repository = TradingStateRepository(database)
    await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )

    local = await repository.local_reconciliation_snapshot()
    matching = await repository.reconcile(local, now=now)
    assert matching.ok is True
    assert matching.safe_mode is False

    mismatch = await repository.reconcile(
        ReconciliationSnapshot(
            balances={"USD": Decimal("99")},
            open_order_ids=frozenset(),
            position_ids=frozenset({"exchange-position"}),
            recent_fill_ids=frozenset(),
        ),
        now=now,
    )
    assert mismatch.safe_mode is True
    assert "balance_mismatch:USD" in mismatch.mismatches
    assert "positions_mismatch" in mismatch.mismatches
    await database.close()


async def test_reconciliation_uses_spot_asset_identity_for_open_local_position(tmp_path) -> None:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'reconciliation-position.db'}")
    await database.initialize()
    repository = TradingStateRepository(database)
    intent = _intent(now)
    paper = SimulatedBroker(FixedClock(now))
    order = await ExecutionEngine(paper).execute(intent, _decision(now))
    await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    await repository.record_execution(intent=intent, order=order, now=now)

    local = await repository.local_reconciliation_snapshot()
    assert local.position_ids == frozenset({"equity:QQQ"})
    exchange = local.model_copy()
    result = await repository.reconcile(exchange, now=now)
    assert result.ok is True
    assert result.safe_mode is False
    await database.close()


async def test_restart_missing_protective_stop_enters_safe_mode(tmp_path) -> None:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'missing-stop.db'}")
    await database.initialize()
    repository = TradingStateRepository(database)
    intent = _intent(now)
    paper = SimulatedBroker(FixedClock(now))
    order = await ExecutionEngine(paper).execute(intent, _decision(now))
    await repository.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("100"),
        asset="QQQ",
        now=now,
    )
    await repository.record_execution(intent=intent, order=order, now=now)
    position = (await repository.open_positions())[0]
    await repository._audit.append(  # type: ignore[attr-defined]
        "positions",
        {
            **position,
            "protective_stop_active": False,
            "state_at": (now.replace(second=1)).isoformat(),
            "state_priority": 3,
        },
        created_at=now.replace(second=1),
        asset="QQQ",
        event_time=now.replace(second=1),
        received_time=now.replace(second=1),
        processed_time=now.replace(second=1),
    )
    recovery = await repository.protective_stop_recovery()
    assert recovery.safe_mode is True
    assert recovery.unprotected_position_ids == (str(position["position_id"]),)
    result = await repository.reconcile(
        await repository.local_reconciliation_snapshot(),
        now=now,
    )
    assert result.safe_mode is True
    assert f"protective_stop_missing:{position['position_id']}" in result.mismatches
    await database.close()
