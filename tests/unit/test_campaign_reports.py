"""Replay hardening (Monte Carlo, macro gate), paper vs reality and campaign reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.db.database import Database
from trading_bot.macro.calendar import MacroEvent
from trading_bot.reports.campaign import daily_report, live_readiness
from trading_bot.simulation.monte_carlo import monte_carlo
from trading_bot.simulation.reality import paper_reality

NOW = datetime(2026, 9, 28, 19, 0, tzinfo=UTC)


def test_monte_carlo_is_deterministic_and_bounded() -> None:
    pnls = [Decimal(v) for v in ("3", "-2", "4", "-2", "-2", "5", "1", "-1", "2", "-3")]
    first, second = monte_carlo(pnls), monte_carlo(pnls)
    assert first == second  # fixed seed: same trades, same report
    assert first is not None
    assert first.final_pnl_p5 <= first.final_pnl_p50 <= first.final_pnl_p95
    assert first.max_drawdown_p95 >= first.max_drawdown_p50 >= 0
    assert first.expectancy_ci_low <= Decimal("0.5") <= first.expectancy_ci_high
    assert monte_carlo(pnls[:3]) is None  # too few trades to say anything


def test_paper_reality_keeps_paper_and_adjusted_pnl_apart() -> None:
    proposal = {"payload": {"proposal_id": "abcdefghijklmnopqrstuvwxyz", "entry_price": "480",
                            "observed_spread_bps": "1"}}
    order = {"payload": {
        "client_order_id": "hyverion-abcdefghijklmnopqrst", "side": "buy",
        "fills": [{"price": "480.00", "quantity": "10"}],
    }}
    exit_order = {"payload": {"client_order_id": "hyverion-exit-x", "side": "sell",
                              "fills": [{"price": "482", "quantity": "10"}]}}
    report = paper_reality([order, exit_order], [proposal], realized_pnl_usd=Decimal("20"))
    assert report.fills_compared == 1
    assert report.realized_slippage_bps_mean == Decimal("0.00")
    assert report.paper_kinder_than_model is True
    # 2.5 bps modelled on 4800 USD = 1.20 USD a live fill would plausibly have paid.
    assert report.adjusted_simulated_pnl_usd == Decimal("18.80")
    assert report.broker_paper_pnl_usd == Decimal("20")


@pytest.mark.asyncio
async def test_reports_on_an_empty_ledger_never_claim_readiness(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'reports.db'}")
    await database.initialize()
    try:
        daily = await daily_report(database, now=NOW)
        assert daily.entries == 0 and daily.broker_paper_pnl_usd == 0
        assert daily.notes
        readiness = await live_readiness(database, now=NOW)
        assert readiness.verdict == "NOT_READY" and readiness.live_enabled is False
        review = next(item for item in readiness.items if item.item == "Revisión humana")
        assert review.status == "PENDING"
    finally:
        await database.close()


def test_replay_applies_the_macro_gate_on_published_schedules() -> None:
    import asyncio

    from trading_bot.config import load_settings
    from trading_bot.core.clock import FixedClock
    from trading_bot.market_data.fixture import FixtureMarketData
    from trading_bot.simulation.strategy_replay import replay_strategies
    from trading_bot.strategies.registry import strategy_factories

    end = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
    fixture = FixtureMarketData(FixedClock(end))
    from trading_bot.market.clock import regular_session_only

    candles = regular_session_only(
        asyncio.run(fixture.fetch_candle_range("QQQ", start=end - timedelta(days=7), end=end))
    )
    config = load_settings().public.strategies.model_copy(update={"enabled": ("trend_momentum",)})
    plain = replay_strategies(strategy_factories(config), candles, capital=Decimal("10000"))
    # A HIGH event every regular-session hour blocks almost every entry.
    events = [
        MacroEvent(event_id=f"e{i}", event_type="CPI", title="CPI",
                   scheduled_at=candles[0].event_time + timedelta(minutes=30 * i),
                   importance="HIGH", source="bls")
        for i in range(0, 12 * 24 * 2)
    ]
    gated = replay_strategies(
        strategy_factories(config), candles, capital=Decimal("10000"), macro_events=events
    )
    assert gated.plugins[0].macro_blocked > 0
    assert gated.plugins[0].out_of_sample.trades <= plain.plugins[0].out_of_sample.trades
    assert any("Puerta macro aplicada" in line for line in gated.assumptions)
