from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import control_client, make_context, make_proposal

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.core.orchestrator import MasterOrchestrator, default_paper_context
from trading_bot.data.event_detector import EventDetector
from trading_bot.data.features import FeatureEngine
from trading_bot.data.storage import ParquetMarketStore
from trading_bot.data.stream import PublicStreamCoordinator, ReplayGapMonitor
from trading_bot.db import AuditRepository, Database, TradingStateRepository
from trading_bot.learning.optimizer import (
    ChampionChallenger,
    ChangeProposalWorkflow,
    ExperimentMetrics,
)
from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.positions import DeterministicPositionManager, PositionState
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.learning import ChangeProposal
from trading_bot.schemas.trading import Candle, MarketSnapshot
from trading_bot.simulation.backtest import BacktestEngine
from trading_bot.strategies.signal_score import compute_signal_score
from trading_bot.strategies.trend_momentum import TrendMomentumStrategy


def make_snapshot(now: datetime, *, spread: Decimal = Decimal("0.01")) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="QQQ",
        bid=Decimal("104.99"),
        ask=Decimal("105") + spread,
        last=Decimal("105"),
        session_volume=Decimal("25000"),
        session_dollar_volume=Decimal("25000000"),
        event_time=now,
        received_time=now,
        processed_time=now,
    )


def test_feature_event_strategy_and_critic(clock, risk_config, now) -> None:
    prices = tuple(Decimal(value) for value in ("100", "101", "102", "103", "105"))
    features = FeatureEngine().compute("QQQ", prices)
    assert features.fast_sma > features.slow_sma
    detection = EventDetector().detect(make_snapshot(now), features)
    assert detection.interesting
    assert "price_displacement" in detection.triggers

    settings = load_settings()
    strategy = TrendMomentumStrategy(
        settings.public.strategies.signal_weights,
        settings.public.strategies.signal_weights_version,
        clock,
    )
    proposal = strategy.propose(make_snapshot(now), features, risk_budget_usd=Decimal("10"))
    assert proposal is not None
    assert proposal.expected_net_value_usd > 0
    review = DeterministicCritic(clock, risk_config.max_spread_bps).review(
        proposal, make_snapshot(now)
    )
    assert review.verdict == "APPROVE"
    rejected = DeterministicCritic(clock, Decimal("0")).review(proposal, make_snapshot(now))
    assert rejected.verdict == "REJECT"
    assert "spread_exceeds_limit" in rejected.critical_conflicts


def test_feature_and_signal_validation_branches(clock, now) -> None:
    with pytest.raises(ValueError, match="positive prices"):
        FeatureEngine().compute("QQQ", (Decimal("1"),) * 4)
    with pytest.raises(ValueError, match="positive prices"):
        FeatureEngine().compute(
            "QQQ",
            (Decimal("1"), Decimal("0"), Decimal("1"), Decimal("1"), Decimal("1")),
        )
    components = make_proposal(now).signal_components
    with pytest.raises(ValueError, match="invalid signal weights"):
        compute_signal_score(components, {"technical": Decimal("1")})
    with pytest.raises(ValueError, match="sum exactly"):
        compute_signal_score(
            components,
            {
                name: Decimal("0")
                for name in components.model_dump(exclude={"weights_version"})
            },
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        make_snapshot(datetime(2026, 9, 14, 12, 0))
    with pytest.raises(ValueError, match="OHLC"):
        Candle(
            symbol="QQQ",
            interval="1m",
            open=Decimal("10"),
            high=Decimal("9"),
            low=Decimal("8"),
            close=Decimal("10"),
            volume=Decimal("1"),
            trades=1,
            event_time=now,
            received_time=now,
            processed_time=now,
        )


def test_position_manager_and_session_guardian(clock, risk_config, now) -> None:
    position = PositionState(
        position_id="p1",
        asset="QQQ",
        side=Side.BUY,
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        quantity=Decimal("1"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        opened_at=now,
        protective_stop_active=True,
    )
    manager = DeterministicPositionManager(clock)
    assert manager.evaluate(position, data_fresh=False).action == "HOLD"
    assert (
        manager.evaluate(
            position.model_copy(update={"current_price": Decimal("98")}), data_fresh=True
        ).action
        == "EMERGENCY_EXIT"
    )
    assert (
        manager.evaluate(
            position.model_copy(update={"current_price": Decimal("103")}), data_fresh=True
        ).action
        == "TAKE_PROFIT"
    )
    assert (
        manager.evaluate(
            position.model_copy(update={"protective_stop_active": False}), data_fresh=True
        ).action
        == "EMERGENCY_EXIT"
    )

    from trading_bot.risk.session_guardian import SessionGuardian

    guardian = SessionGuardian(risk_config, clock)
    assert guardian.evaluate(make_context(live_stopped=True)).action == "STOP_LIVE_FOR_DAY"
    assert guardian.evaluate(make_context(losing_streak=3)).action == "STOP_LIVE_FOR_DAY"
    assert (
        guardian.evaluate(make_context(realized_net_pnl_today=Decimal("300"))).action
        == "REDUCE_RISK"
    )
    assert guardian.evaluate(make_context(cooldown_active=True)).action == "PAUSE"


def test_backtest_shadow_learning_and_parquet(tmp_path, now) -> None:
    result = BacktestEngine().run_buy_and_hold_baseline(
        tuple(Decimal(value) for value in ("100", "101", "99", "103", "105")),
        capital=Decimal("1000"),
    )
    assert result.trades == 1
    assert result.fees_usd > 0
    from trading_bot.simulation.shadow import ShadowComparison

    comparison = ShadowComparison.compare("p1", Decimal("4"), Decimal("6"))
    assert comparison.incremental_value_usd == Decimal("2")
    assert ShadowComparison.compare("p2", Decimal("4"), None).incremental_value_usd is None

    candidate = ChangeProposal(
        id="cp1",
        agent="strategy",
        current_version="1",
        candidate_version="2",
        reason="test",
        evidence=("replay",),
        affected_rules=("weights",),
        expected_improvement="higher expectancy",
        risk="overfit",
        candidate_spec={},
        created_at=now,
    )
    workflow = ChangeProposalWorkflow()
    candidate = workflow.transition(candidate, "TESTING")
    candidate = workflow.transition(candidate, "READY_FOR_REVIEW")
    assert workflow.transition(candidate, "REJECTED").status == "REJECTED"
    with pytest.raises(ValueError):
        workflow.transition(candidate, "DEPLOYED")
    metrics = ChampionChallenger().ready_for_review(
        ExperimentMetrics(Decimal("1"), Decimal("10"), Decimal("0.2"), 100),
        ExperimentMetrics(Decimal("2"), Decimal("9"), Decimal("0.1"), 100),
    )
    assert metrics

    store = ParquetMarketStore(tmp_path)
    path = store.write_batch("candles", [{"symbol": "QQQ", "close": 105}], now)
    assert path.exists()
    query = "SELECT symbol, close FROM read_parquet(?)"
    assert store.query(query, (str(path),)) == [("QQQ", 105)]
    with pytest.raises(ValueError):
        store.write_batch("candles", [], now)
    with pytest.raises(ValueError):
        store.query("DELETE FROM candles")


@pytest.mark.asyncio
async def test_orchestrator_paper_cycle_persists_audit(tmp_path, clock, risk_config, now) -> None:
    settings = load_settings()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    await database.initialize()
    metrics = MetricsRegistry()
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=AuditRepository(database),
        clock=clock,
        metrics=metrics,
    )
    result = await orchestrator.run_cycle(
        make_snapshot(now),
        tuple(Decimal(value) for value in ("100", "101", "102", "103", "105")),
        default_paper_context(Decimal("10000")),
    )
    assert result.status == "EXECUTED"
    assert result.order is not None
    assert (await AuditRepository(database).recent("orders"))[0]["asset"] == "QQQ"
    samples = {(sample.name, sample.labels): sample.value for sample in metrics.snapshot()}
    assert samples[("trading_cycles_total", (("mode", "paper"), ("status", "EXECUTED")))] == 1
    assert samples[("risk_decisions_total", (("verdict", "ALLOW"),))] == 1
    await database.close()


@pytest.mark.asyncio
async def test_orchestrator_protects_and_closes_position_before_new_entry(
    tmp_path, clock, risk_config, now
) -> None:
    settings = load_settings()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'position-exit.db'}")
    await database.initialize()
    audit = AuditRepository(database)
    state = TradingStateRepository(database)
    context = await state.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("10000"),
        asset="QQQ",
        now=now,
    )
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=audit,
        clock=clock,
        state_repository=state,
    )
    first = await orchestrator.run_cycle(
        make_snapshot(now),
        tuple(Decimal(value) for value in ("100", "101", "102", "103", "105")),
        context,
    )
    assert first.status == "EXECUTED"
    position = (await state.open_positions())[0]
    target = Decimal(str(position["target_price"]))
    await state.mark_to_market(asset="QQQ", current_price=target, now=now)
    next_snapshot = make_snapshot(now).model_copy(
        update={
            "bid": target - Decimal("0.01"),
            "ask": target,
            "last": target,
        }
    )
    next_context = await state.risk_context(
        mode=TradingMode.PAPER,
        starting_equity=Decimal("10000"),
        asset="QQQ",
        now=now,
    )
    second = await orchestrator.run_cycle(
        next_snapshot,
        tuple(Decimal(value) for value in ("103", "105", "106", "107", str(target))),
        next_context,
    )
    assert second.status == "POSITION_EXITED"
    assert (await state.open_positions()) == []
    await database.close()


@pytest.mark.asyncio
async def test_orchestrator_denied_proposal_is_replayed_as_shadow(
    tmp_path, clock, risk_config, now
) -> None:
    settings = load_settings()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'shadow.db'}")
    await database.initialize()
    repository = AuditRepository(database)
    result = await MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=repository,
        clock=clock,
    ).run_cycle(
        make_snapshot(now),
        tuple(Decimal(value) for value in ("100", "101", "102", "103", "105")),
        default_paper_context(Decimal("100")).model_copy(update={"cooldown_active": True}),
    )

    assert result.status == "REJECTED"
    shadow_rows = await repository.recent("shadow_trades")
    assert shadow_rows[0]["payload"]["exit_reason"] in {"target", "stop", "horizon_expired"}
    await database.close()


@pytest.mark.asyncio
async def test_orchestrator_rejects_model_score_that_bypasses_configured_weights(
    tmp_path, clock, risk_config, now
) -> None:
    settings = load_settings()
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'invalid-score.db'}")
    await database.initialize()
    base = TrendMomentumStrategy(settings.public.strategies.signal_weights, "v1", clock)

    class InvalidScoreStrategy:
        def propose(self, snapshot, features, *, risk_budget_usd):
            proposal = base.propose(snapshot, features, risk_budget_usd=risk_budget_usd)
            assert proposal is not None
            return proposal.model_copy(update={"signal_score": proposal.signal_score + 1})

    result = await MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=InvalidScoreStrategy(),
        critic=DeterministicCritic(clock, risk_config.max_spread_bps),
        risk_engine=RiskEngine(risk_config, clock),
        execution_engine=ExecutionEngine(SimulatedBroker(clock)),
        repository=AuditRepository(database),
        clock=clock,
        signal_weights=settings.public.strategies.signal_weights,
        signal_weights_version=settings.public.strategies.signal_weights_version,
    ).run_cycle(
        make_snapshot(now),
        tuple(Decimal(value) for value in ("100", "101", "102", "103", "105")),
        default_paper_context(Decimal("10000")),
    )

    assert result.status == "AI_PIPELINE_FAILED"
    event = (await AuditRepository(database).recent("system_events"))[0]["payload"]
    assert event["status"] == "SIGNAL_SCORE_INVALID"
    assert not await AuditRepository(database).recent("risk_decisions")
    await database.close()


def test_replay_gap_monitor_rejects_duplicate_regressed_gapped_and_stale_frames(now) -> None:
    monitor = ReplayGapMonitor(
        max_gap_seconds=Decimal("5"), stale_after_seconds=Decimal("10")
    )
    assert monitor.observe(make_snapshot(now), now=now).status == "OK"
    assert monitor.observe(make_snapshot(now), now=now).status == "DUPLICATE"
    assert (
        monitor.observe(make_snapshot(now - timedelta(seconds=1)), now=now).status
        == "OUT_OF_ORDER"
    )
    assert (
        monitor.observe(
            make_snapshot(now + timedelta(seconds=8)), now=now + timedelta(seconds=8)
        ).status
        == "GAP"
    )
    assert (
        monitor.observe(
            make_snapshot(now + timedelta(seconds=9)), now=now + timedelta(seconds=25)
        ).status
        == "STALE"
    )


@pytest.mark.asyncio
async def test_public_stream_coordinator_delivers_assessment_and_stops(now) -> None:
    now = datetime.now(UTC)
    class FakeAdapter:
        def stream_snapshots(
            self,
            symbol: str,
            *,
            max_reconnect_delay_seconds: int,
            stop_event: asyncio.Event,
        ) -> AsyncIterator[MarketSnapshot]:
            del max_reconnect_delay_seconds

            async def iterator() -> AsyncIterator[MarketSnapshot]:
                yield make_snapshot(now).model_copy(update={"symbol": symbol})
                stop_event.set()

            return iterator()

    seen: list[tuple[str, str]] = []

    async def handler(snapshot: MarketSnapshot, assessment) -> None:
        seen.append((snapshot.symbol, assessment.status))

    stop_event = asyncio.Event()
    await PublicStreamCoordinator(FakeAdapter(), monitor=ReplayGapMonitor()).run(
        ("QQQ",), stop_event=stop_event, handler=handler
    )
    assert seen == [("QQQ", "OK")]


def test_dashboard_health_and_mode(tmp_path) -> None:
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'dashboard.db'}"}
                    )
                }
            )
        }
    )
    with control_client(create_control_api(settings)) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json()["status"] == "ready"
        page = client.get("/")
        assert page.status_code == 200
        assert page.json()["ui"] == "native"
        assert page.json()["live_trading"] is False
